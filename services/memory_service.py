"""
用户长期记忆服务层

记忆按客户（手机号）隔离：要点写入 user_memories（含语义向量），
新消息到达时按 recall_score 召回 Top-5 注入 LLM 上下文。
Embedding 不可用（无 Key / 网络异常）时语义分为 None，自动降级为时效+重要度排序。
"""

import re
import logging
from typing import Any, Dict, List, Optional

import numpy as np

from db.db_router import DatabaseRouter
from config.time_config import TimeConfig
from .memory_scoring import rank_top_k, RECENCY_WINDOW_DAYS

logger = logging.getLogger(__name__)

PHONE_PATTERN = re.compile(r'1\d{10}')

DEFAULT_IMPORTANCE = {'repair': 0.8, 'preference': 0.6, 'consult': 0.5, 'profile': 0.7}

# 画像记忆向量去重阈值：新画像与现存画像余弦相似度高于该值时视为重复，跳过重写
PROFILE_DEDUPE_SIMILARITY = 0.92


class MemoryService:
    """用户长期记忆服务类"""

    def __init__(self, db_path: str = 'sqlite:///data/smart_appointment.db'):
        self.db_router = DatabaseRouter(db_path)
        self.memory_repo = self.db_router.user_memories
        self.session_repo = self.db_router.chat_sessions
        self._embedding_ready: Optional[bool] = None  # 首次探测后缓存

    # ---------- 工具 ----------

    @staticmethod
    def extract_phone(text: str) -> Optional[str]:
        """从文本提取第一个 11 位手机号（1 开头）"""
        match = PHONE_PATTERN.search(text or '')
        return match.group(0) if match else None

    def _embed_or_none(self, text: str) -> Optional[List[float]]:
        """生成语义向量；Embedding 不可用时返回 None（首次失败后本次运行不再重试）"""
        if self._embedding_ready is False:
            return None
        try:
            from .text_embedding import embed_input

            vector = embed_input(text)
            self._embedding_ready = True
            return vector
        except Exception as e:
            if self._embedding_ready is None:
                logger.warning(f"Embedding 服务不可用，记忆语义召回降级为时效+重要度: {e}")
                self._embedding_ready = False
            return None

    # ---------- 记忆写入 ----------

    def add_memory(self, user_id: str, content: str, memory_type: str = 'consult',
                   importance: Optional[float] = None, source_session_id: Optional[str] = None) -> bool:
        """写入一条长期记忆（内容要点 + 可选语义向量；同用户同内容同类型去重）"""
        try:
            if not user_id or not content:
                return False
            recent = self.memory_repo.get_user_memories(user_id, limit=20)
            if any(m.get('content') == content and m.get('memory_type') == memory_type
                   for m in recent):
                return True
            importance = importance if importance is not None else DEFAULT_IMPORTANCE.get(memory_type, 0.5)
            embedding = self._embed_or_none(content)
            self.memory_repo.add_memory(
                user_id=user_id,
                content=content,
                memory_type=memory_type,
                importance=importance,
                embedding=embedding,
                source_session_id=source_session_id,
            )
            return True
        except Exception as e:
            logger.error(f"写入长期记忆失败：user={user_id}，{e}")
            return False

    def list_memories(self, user_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """列出某客户的活跃记忆"""
        try:
            return self.memory_repo.get_user_memories(user_id, limit=limit)
        except Exception as e:
            logger.error(f"读取长期记忆失败：user={user_id}，{e}")
            return []

    def upsert_profile_memory(self, user_id: str, content: str,
                              source_session_id: str = 'dream') -> str:
        """沉淀/刷新客户画像记忆（memory_type=profile，每人至多一条活跃）：
        内容一致或向量相似(>=0.92)视为重复 → 返回 'duplicate'；
        画像有实质变化 → 软删除旧画像后写新条 → 'superseded'；
        无历史画像 → 直接写入 → 'new'。
        """
        try:
            if not user_id or not content:
                return 'invalid'
            current = [m for m in self.list_memories(user_id, limit=50)
                       if m.get('memory_type') == 'profile']
            new_embedding = self._embed_or_none(content)
            if current:
                newest = current[0]  # 仓库按 created_at 倒序
                if newest.get('content') == content:
                    return 'duplicate'
                old_embedding = newest.get('embedding')
                if new_embedding is not None and old_embedding:
                    similarity = self._cosine_similarity(new_embedding, old_embedding)
                    if similarity >= PROFILE_DEDUPE_SIMILARITY:
                        return 'duplicate'
                for mem in current:
                    self.memory_repo.delete_memory(mem['id'])
            self.memory_repo.add_memory(
                user_id=user_id,
                content=content,
                memory_type='profile',
                importance=DEFAULT_IMPORTANCE['profile'],
                embedding=new_embedding,
                source_session_id=source_session_id,
            )
            return 'superseded' if current else 'new'
        except Exception as e:
            logger.error(f"沉淀画像记忆失败：user={user_id}，{e}")
            return 'error'

    # ---------- 会话绑定 ----------

    def maybe_bind_session(self, session_id: str, text: str) -> Optional[str]:
        """会话中出现合法手机号时绑定到客户；返回绑定后的 user_id（未命中返回 None）"""
        try:
            phone = self.extract_phone(text)
            if phone is None:
                return None
            self.session_repo.update_session_user(session_id, phone)
            logger.info(f"会话 {session_id} 绑定客户 {phone}")
            return phone
        except Exception as e:
            logger.error(f"会话绑定用户失败：{e}")
            return None

    # ---------- 召回 ----------

    def recall(self, user_id: str, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """按召回分（0.6 语义 + 0.3 时效 + 0.1 重要度）取 Top-5 长期记忆"""
        try:
            memories = self.memory_repo.get_user_memories(user_id, limit=50)
            if not memories:
                return []

            query_embedding = self._embed_or_none(query) if query else None
            now = TimeConfig.naive_now()
            candidates = []
            for mem in memories:
                semantic = None
                if query_embedding is not None and mem.get('embedding'):
                    semantic = self._cosine_similarity(query_embedding, mem['embedding'])
                created_at = mem.get('created_at')
                age_days = (now - created_at).days if created_at else RECENCY_WINDOW_DAYS
                candidates.append({
                    'content': mem['content'],
                    'memory_type': mem.get('memory_type', 'consult'),
                    'semantic': semantic,
                    'age_days': float(age_days),
                    'importance': float(mem.get('importance') or 0.5),
                })
            return rank_top_k(candidates, top_k=top_k)
        except Exception as e:
            logger.error(f"召回长期记忆失败：user={user_id}，{e}")
            return []

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        a = np.asarray(a, dtype='float32')
        b = np.asarray(b, dtype='float32')
        norm = float(np.linalg.norm(a) * np.linalg.norm(b))
        if norm == 0:
            return 0.0
        return float(np.dot(a, b) / norm)
