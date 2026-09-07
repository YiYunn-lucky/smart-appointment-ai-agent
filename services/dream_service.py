"""
AutoDream 离线沉淀服务

把分散在多会话/多时段的客户行为离线回放成可召回的记忆画像：
1. 资格判定：累计 >= 5 次会话且首次到最近一次活动跨度 >= 24h（老客户才沉淀）
2. 增量回放：只处理事件ID大于 checkpoint 的新行为（幂等，中断可续跑）
3. 置信度更新：新事件逐条累计 user_preferences（工程师/品类/故障/时段）
4. 冲突降权：维度本周期只出现单一新值时，同维度其他旧值置信度减半（偏好漂移）
5. 画像沉淀：聚合全量报修事件 → LLM 润色（无 Key 走模板）→ user_memories(profile)
   （向量/内容去重，每人至多一条活跃画像）
6. 任务锁：checkpoint 行级锁 + 进程内线程锁，崩溃残留锁超时自动接管

调度风格与 RecommendationService 对齐：后台守护线程定时扫描 + run_immediate_check 手动触发。
"""

import asyncio
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

from db.db_router import DatabaseRouter
from .dream_policy import (
    MIN_SESSIONS,
    MIN_SPAN_HOURS,
    activity_stats,
    aggregate_profile,
    build_profile_prompt,
    build_profile_text,
    is_eligible,
    preference_upsert_deltas,
    replay_events,
    stale_downweight_rows,
)

# 后台调度扫描间隔（分钟）
SCAN_INTERVAL_MINUTES = 60


async def _default_profile_llm(user_id: str, profile: Dict[str, Any],
                               engineer_names: Dict[str, str]) -> Optional[str]:
    """默认画像润色通道：LangChain 聊天模型单轮生成（离线无 Key 时抛错走模板兜底）"""
    from config.model_provider import create_chat_model

    llm = create_chat_model(temperature=0.5)
    prompt = build_profile_prompt(user_id, profile, engineer_names)
    response = await llm.ainvoke([{"role": "user", "content": prompt}])
    text = (response.content or "").strip().strip('"')
    return text[:200] if text else None


class DreamService:
    """AutoDream 离线沉淀服务类"""

    def __init__(self, db_path: str = 'sqlite:///data/smart_appointment.db',
                 audit_logger: Optional[Callable[..., Any]] = None,
                 audit_actor: str = 'dream_scheduler'):
        self.db_path = db_path
        self.db_router = DatabaseRouter(db_path)
        self.behavior_repo = self.db_router.user_behavior
        self.checkpoint_repo = self.db_router.dream_checkpoints
        self.chat_session_repo = self.db_router.chat_sessions
        # M15 审计回调：每次实际沉淀（consolidated / error）留痕；跳过与占锁不记录
        self.audit_logger = audit_logger
        self.audit_actor = audit_actor

        self.is_running = False
        self.scheduler_thread = None
        self._thread_locks: Dict[str, threading.Lock] = {}
        # 画像润色 LLM 通道；置 None 关闭 LLM（离线测试 / 无 Key 环境走模板兜底）
        self.profile_text_llm = _default_profile_llm

    def _audit(self, action: str, resource_id: Optional[str] = None,
               result: str = 'ok', detail: Optional[str] = None) -> None:
        """AutoDream 沉淀审计（成功/失败）；审计异常只告警不阻断沉淀"""
        if self.audit_logger is None:
            return
        try:
            self.audit_logger(
                actor=self.audit_actor,
                scene='auto_dream',
                action=action,
                resource_type='user_preference',
                resource_id=resource_id,
                tool_id=None,
                risk_tier='write',
                result=result,
                detail=detail,
            )
        except Exception as e:
            logger.warning(f"AutoDream 审计失败（不影响沉淀）：{action} {e}")

    # ---------- 单客户沉淀 ----------

    def _thread_lock(self, user_id: str) -> threading.Lock:
        """进程内每人一把锁，防止同进程并发重复沉淀同一客户"""
        if user_id not in self._thread_locks:
            self._thread_locks[user_id] = threading.Lock()
        return self._thread_locks[user_id]

    def _load_engineer_names(self, engineer_ids: List[Any]) -> Dict[str, str]:
        names: Dict[str, str] = {}
        try:
            from db.db_router import EngineerDBRouter

            db = EngineerDBRouter(db_path=self.db_path)
            for eid in engineer_ids:
                info = db.get_engineer_by_id(int(eid))
                if info and info.get('name'):
                    names[str(eid)] = info['name']
        except Exception as e:
            logger.warning(f"读取工程师姓名失败：{e}")
        return names

    def consolidate_user(self, user_id: str, force: bool = False) -> Dict[str, Any]:
        """对单个客户执行一次 AutoDream 沉淀（资格不足/无新事件时安全跳过）"""
        try:
            behavior_rows = self.behavior_repo.get_user_behaviors(user_id)
            chat_rows = self.chat_session_repo.list_sessions(user_id=user_id)
            stats = activity_stats(chat_rows, behavior_rows)

            if not force and not is_eligible(stats):
                return {
                    'user_id': user_id, 'status': 'skipped_not_eligible',
                    'session_count': stats['session_count'],
                    'span_hours': round(stats['span_hours'], 1),
                }

            lock = self._thread_lock(user_id)
            with lock:
                if not self.checkpoint_repo.try_acquire_lock(user_id):
                    return {'user_id': user_id, 'status': 'locked'}

                try:
                    result = self._consolidate_locked(user_id, stats)
                    if result.get('status') == 'consolidated':
                        self._audit(action='consolidate', resource_id=user_id,
                                    detail=(f"回放 {result.get('events_replayed', 0)} 事件，"
                                            f"降权 {result.get('decayed_count', 0)} 行，"
                                            f"画像={result.get('profile_memory')}"))
                    elif result.get('status') == 'error':
                        self._audit(action='consolidate', resource_id=user_id, result='error',
                                    detail=str(result.get('error', ''))[:200])
                    return result
                except Exception as e:
                    logger.error(f"AutoDream 沉淀失败：user={user_id}，{e}")
                    self.checkpoint_repo.release_lock(user_id, status='error', error=str(e))
                    self._audit(action='consolidate', resource_id=user_id, result='error',
                                detail=str(e)[:200])
                    return {'user_id': user_id, 'status': 'error', 'error': str(e)}
        except Exception as e:
            logger.error(f"AutoDream 资格判定失败：user={user_id}，{e}")
            return {'user_id': user_id, 'status': 'error', 'error': str(e)}

    def _consolidate_locked(self, user_id: str, stats: Dict[str, Any]) -> Dict[str, Any]:
        """已持锁的沉淀主流程：增量回放 → 置信度/降权 → 画像记忆 → checkpoint"""
        checkpoint = self.checkpoint_repo.get_or_create(user_id)
        behavior_rows = self.behavior_repo.get_user_behaviors(user_id)
        events = replay_events(behavior_rows, since_event_id=checkpoint['last_event_id'])

        if not events:
            self.checkpoint_repo.release_lock(user_id, status='no_new_events')
            return {'user_id': user_id, 'status': 'no_new_events',
                    'session_count': stats['session_count'],
                    'span_hours': round(stats['span_hours'], 1)}

        # 1) 置信度更新：新事件逐条累计（同值 +1，与运行时偏好口径一致）
        deltas = preference_upsert_deltas(events)
        for dim, value_counts in deltas.items():
            for value, count in value_counts.items():
                for _ in range(count):
                    self.behavior_repo.update_user_preference(user_id, dim, value)

        # 2) 冲突降权：本周期只出现单一新值的维度，其旧值整体减半（下限 1）
        stored = self.behavior_repo.get_user_preferences(user_id)
        decayed = []
        for pref in stale_downweight_rows(deltas, stored):
            if self.behavior_repo.decay_preference_confidence(pref['id']):
                decayed.append({'type': pref['preference_type'],
                                'value': pref['preference_value'],
                                'old_confidence': pref['confidence_score']})

        # 3) 画像记忆：全量聚合 → LLM 润色（失败走模板）→ 去重轮换写入
        profile = aggregate_profile(behavior_rows)
        all_engineer_ids = [value for value, _ in profile.get('engineer_id', [])]
        engineer_names = self._load_engineer_names(all_engineer_ids)
        profile_text = self._generate_profile_text(user_id, profile, engineer_names)

        from services.memory_service import MemoryService
        memory_result = MemoryService(self.db_path).upsert_profile_memory(
            user_id, profile_text, source_session_id='dream')

        # 4) 幂等 checkpoint：记录已回放到的事件ID
        last_event_id = events[-1]['id']
        self.checkpoint_repo.release_lock(
            user_id, processed_events=len(events), last_event_id=last_event_id,
            status='ok')
        logger.info(f"AutoDream 沉淀完成：user={user_id}，回放 {len(events)} 事件，"
                    f"画像记忆={memory_result}，降权 {len(decayed)} 行")
        return {
            'user_id': user_id, 'status': 'consolidated',
            'events_replayed': len(events),
            'preference_deltas': {k: v for k, v in deltas.items() if v},
            'decayed_count': len(decayed),
            'profile_memory': memory_result,
            'session_count': stats['session_count'],
            'span_hours': round(stats['span_hours'], 1),
        }

    def _generate_profile_text(self, user_id: str, profile: Dict[str, Any],
                               engineer_names: Dict[str, str]) -> str:
        """画像文案：LLM 润色优先，异常/无 Key 时确定性模板兜底"""
        llm_text = None
        if self.profile_text_llm is not None:
            try:
                llm_text = asyncio.run(
                    self.profile_text_llm(user_id, profile, engineer_names))
            except Exception as e:
                logger.warning(f"画像 LLM 润色失败，走模板兜底：{type(e).__name__}: {e}")
        if llm_text:
            return llm_text
        return build_profile_text(profile, user_id=user_id, engineer_names=engineer_names)

    # ---------- 批量扫描 ----------

    def scan_and_consolidate(self, limit_users: int = 200) -> List[Dict[str, Any]]:
        """扫描全部有行为记录的客户，逐个执行沉淀；返回非跳过项的结果列表"""
        results = []
        try:
            user_ids = self.behavior_repo.list_user_ids(limit=limit_users)
            for user_id in user_ids:
                result = self.consolidate_user(user_id)
                if result.get('status') not in ('skipped_not_eligible', 'no_new_events'):
                    results.append(result)
        except Exception as e:
            logger.error(f"AutoDream 批量扫描失败：{e}")
        return results

    # ---------- 调度器 ----------

    def start_scheduler(self) -> bool:
        """后台守护线程：定期扫描待沉淀客户（资格不满足自动跳过，开销极小）"""
        if self.is_running:
            logger.warning("AutoDream 调度器已在运行")
            return False
        self.is_running = True

        def run_loop():
            logger.info(f"AutoDream 沉淀调度器已启动（每 {SCAN_INTERVAL_MINUTES} 分钟扫描一次）")
            while self.is_running:
                try:
                    results = self.scan_and_consolidate()
                    if results:
                        logger.info(f"AutoDream 本轮沉淀 {len(results)} 位客户："
                                    + "；".join(f"{r['user_id']}={r['status']}" for r in results))
                except Exception as e:
                    logger.error(f"AutoDream 定时扫描异常：{e}")
                time.sleep(SCAN_INTERVAL_MINUTES * 60)
            logger.info("AutoDream 沉淀调度器已停止")

        self.scheduler_thread = threading.Thread(target=run_loop, daemon=True,
                                                 name="dream-scheduler")
        self.scheduler_thread.start()
        return True

    def stop_scheduler(self) -> bool:
        self.is_running = False
        logger.info("AutoDream 沉淀调度器已停止")
        return True

    def run_immediate_check(self) -> List[Dict[str, Any]]:
        """立即执行一次沉淀扫描（手动触发/测试）"""
        logger.info("执行 AutoDream 立即沉淀检查...")
        return self.scan_and_consolidate()

    def get_status(self) -> Dict[str, Any]:
        """调度器与沉淀进度状态"""
        checkpoints = []
        try:
            checkpoints = self.checkpoint_repo.list_checkpoints(limit=10)
        except Exception as e:
            logger.error(f"读取沉淀检查点失败：{e}")
        return {
            'is_running': self.is_running,
            'thread_alive': self.scheduler_thread.is_alive() if self.scheduler_thread else False,
            'min_sessions': MIN_SESSIONS,
            'min_span_hours': MIN_SPAN_HOURS,
            'recent_checkpoints': checkpoints,
        }


if __name__ == "__main__":
    print("启动 AutoDream 沉淀调度器测试...")
    service = DreamService()
    service.start_scheduler()
    try:
        time.sleep(300)
    except KeyboardInterrupt:
        print("收到中断信号，停止调度器...")
    finally:
        service.stop_scheduler()
