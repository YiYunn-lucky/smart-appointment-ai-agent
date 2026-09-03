"""
用户行为服务层

职责：
1. 封装用户行为相关的数据库操作
2. 处理用户行为分析业务逻辑（按客户报修记录分析）
3. 提供用户偏好管理服务
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from db.db_router import DatabaseRouter
import logging

logger = logging.getLogger(__name__)


class UserBehaviorService:
    """用户行为服务类"""

    def __init__(self, db_path: str = 'sqlite:///data/smart_appointment.db'):
        self.db_router = DatabaseRouter(db_path)
        self.user_behavior_repo = self.db_router.user_behavior

    def record_behavior(self, user_id: str, action_type: str, action_data: Dict[str, Any] = None,
                        engineer_id=None, session_id: str = "default_session") -> bool:
        """记录用户行为（user_id一般为客户手机号）"""
        try:
            behavior_id = self.user_behavior_repo.record_behavior(
                user_id=user_id,
                action_type=action_type,
                action_data=action_data,
                engineer_id=engineer_id,
                session_id=session_id
            )

            if behavior_id:
                logger.info(f"用户行为记录成功：用户={user_id}, 行为={action_type}, ID={behavior_id}")
                return True
            return False

        except Exception as e:
            logger.error(f"记录用户行为失败：{e}")
            return False

    def get_user_behaviors(self, user_id: str, action_type: str = None,
                           days_back: int = None) -> List[Dict[str, Any]]:
        """获取用户行为记录"""
        try:
            return self.user_behavior_repo.get_user_behaviors(user_id, action_type, days_back)
        except Exception as e:
            logger.error(f"获取用户行为记录失败：{e}")
            return []

    def get_user_preferences(self, user_id: str) -> List[Dict[str, Any]]:
        """获取用户偏好"""
        try:
            return self.user_behavior_repo.get_user_preferences(user_id)
        except Exception as e:
            logger.error(f"获取用户偏好失败：{e}")
            return []

    def update_user_preference(self, user_id: str, preference_type: str,
                               preference_value: str) -> bool:
        """更新用户偏好（已存在则置信度+1，不存在则新建）"""
        try:
            return self.user_behavior_repo.update_user_preference(
                user_id, preference_type, preference_value
            )
        except Exception as e:
            logger.error(f"更新用户偏好失败：{e}")
            return False

    # ===========================================
    # 行为模式分析（以报修记录为数据源）
    # ===========================================

    def analyze_user_patterns(self, user_id: str) -> Dict[str, Any]:
        """分析用户行为模式：报修频率、偏好工程师、常用上门时段"""
        try:
            behaviors = self.get_user_behaviors(user_id, days_back=30)

            if not behaviors:
                return {"pattern": "no_data", "recommendation": "需要更多数据"}

            repair_behaviors = [b for b in behaviors if b.get('action_type') == 'repair']
            freq_analysis = self._analyze_frequency(repair_behaviors)

            # 分析偏好工程师
            preferred_engineer = self._analyze_preferred_engineer(repair_behaviors)

            # 分析时段偏好
            time_preference = self._analyze_time_preference(repair_behaviors)

            return {
                "pattern": "active_user" if len(repair_behaviors) > 2 else "occasional_user",
                "frequency_analysis": freq_analysis,
                "preferred_engineer": preferred_engineer,
                "time_preference": time_preference,
                "total_repairs": len(repair_behaviors),
                "analysis_period_days": 30
            }

        except Exception as e:
            logger.error(f"分析用户行为模式失败：{e}")
            return {"pattern": "analysis_error", "error": str(e)}

    def _analyze_frequency(self, repair_behaviors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """分析报修频率"""
        if not repair_behaviors:
            return {"frequency": "no_repairs", "days_between": 0}

        if len(repair_behaviors) < 2:
            return {"frequency": "single_repair", "days_between": 0}

        # 计算平均间隔天数
        dates = []
        for behavior in repair_behaviors:
            if 'created_at' in behavior:
                try:
                    created_at = behavior['created_at']
                    if isinstance(created_at, str):
                        created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        if created_at.tzinfo:
                            created_at = created_at.replace(tzinfo=None)
                    dates.append(created_at)
                except Exception:
                    continue

        if len(dates) < 2:
            return {"frequency": "insufficient_data", "days_between": 0}

        dates.sort()
        intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        avg_interval = sum(intervals) / len(intervals)

        if avg_interval < 7:
            frequency = "very_frequent"
        elif avg_interval < 30:
            frequency = "frequent"
        elif avg_interval < 90:
            frequency = "regular"
        else:
            frequency = "occasional"

        return {"frequency": frequency, "days_between": avg_interval}

    def _analyze_preferred_engineer(self, repair_behaviors: List[Dict[str, Any]]) -> Optional[str]:
        """分析偏好工程师（服务次数最多者）"""
        engineer_counts = {}

        for behavior in repair_behaviors:
            engineer_id = behavior.get('engineer_id')
            if engineer_id:
                engineer_counts[engineer_id] = engineer_counts.get(engineer_id, 0) + 1

        if not engineer_counts:
            return None

        most_frequent_engineer = max(engineer_counts, key=engineer_counts.get)
        return most_frequent_engineer if engineer_counts[most_frequent_engineer] > 1 else None

    def _analyze_time_preference(self, repair_behaviors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """分析常用上门时段"""
        slots = []
        weekdays = []

        for behavior in repair_behaviors:
            action_data = behavior.get('action_data', {})
            if isinstance(action_data, dict) and 'start_time' in action_data:
                try:
                    start_time = action_data['start_time']
                    if isinstance(start_time, str):
                        start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                    if start_time.tzinfo:
                        start_time = start_time.replace(tzinfo=None)
                    slots.append('上午' if start_time.hour < 12 else '下午')
                    weekdays.append(start_time.weekday())
                except Exception:
                    continue

        if not slots:
            return {"preferred_time_slot": None, "preferred_weekday": None}

        from collections import Counter
        slot_counter = Counter(slots)
        weekday_counter = Counter(weekdays)

        preferred_slot = slot_counter.most_common(1)[0][0] if slot_counter else None
        preferred_weekday = weekday_counter.most_common(1)[0][0] if weekday_counter else None

        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        preferred_weekday_name = weekday_names[preferred_weekday] if preferred_weekday is not None else None

        return {
            "preferred_time_slot": preferred_slot,
            "preferred_weekday": preferred_weekday_name,
            "slot_distribution": dict(slot_counter),
            "weekday_distribution": dict(weekday_counter)
        }
