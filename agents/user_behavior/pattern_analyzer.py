"""
家电售后用户行为分析器

核心功能：
1. 分析客户偏好的上门工程师
2. 分析客户报修的常用产品与常见故障
3. 分析客户常用的上门时段（上午/下午）
4. 判断客户是否需要保养/回访提醒
5. 生成个性化回访消息
"""

from typing import Dict, Any, Optional
from datetime import datetime
import logging

from config.time_config import TimeConfig


class PatternAnalyzer:
    """家电售后用户行为分析器"""

    def __init__(self, behavior_service=None):
        self.behavior_service = behavior_service
        self.logger = logging.getLogger(__name__)

    @property
    def behavior_db(self):
        """向后兼容：直接访问行为数据仓库"""
        if hasattr(self, 'behavior_service') and self.behavior_service:
            return self.behavior_service.user_behavior_repo
        return None

    def analyze_user_preferences(self, user_id: str = "default_user") -> Optional[Dict[str, Any]]:
        """分析客户偏好：偏好工程师、常用产品、常见故障、常用上门时段"""
        try:
            if self.behavior_service:
                repairs = self.behavior_service.get_user_behaviors(
                    user_id=user_id,
                    action_type='repair'
                )
            else:
                repairs = self.behavior_db.get_user_behaviors(
                    user_id=user_id,
                    action_type='repair'
                )

            if not repairs:
                return None

            engineer_counts = {}
            product_counts = {}
            fault_counts = {}
            time_slot_counts = {}

            for repair in repairs:
                data = repair.get('action_data', {}) or {}

                engineer_id = repair.get('engineer_id')
                if engineer_id is not None:
                    engineer_counts[engineer_id] = engineer_counts.get(engineer_id, 0) + 1

                product = data.get('product_type')
                if product:
                    product_counts[product] = product_counts.get(product, 0) + 1

                fault = data.get('fault_desc')
                if fault:
                    fault_counts[fault] = fault_counts.get(fault, 0) + 1

                time_slot = self._parse_time_slot(data.get('start_time'))
                if time_slot:
                    time_slot_counts[time_slot] = time_slot_counts.get(time_slot, 0) + 1

            def _top(counts):
                return max(counts, key=counts.get) if counts else None

            return {
                'favorite_engineer_id': _top(engineer_counts),
                'favorite_product_type': _top(product_counts),
                'favorite_fault_desc': _top(fault_counts),
                'favorite_time_slot': _top(time_slot_counts),
                'total_repairs': len(repairs),
                'last_repair_date': repairs[0]['created_at'] if repairs else None
            }

        except Exception as e:
            self.logger.error(f"分析用户偏好失败: {str(e)}")
            return None

    @staticmethod
    def _parse_time_slot(start_time) -> Optional[str]:
        """从上门开始时间解析上午/下午时段（服务窗口9:00-18:00）"""
        if not start_time:
            return None
        try:
            if isinstance(start_time, str):
                dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            else:
                dt = start_time
            if dt.tzinfo:
                dt = dt.astimezone(TimeConfig.BEIJING_TZ).replace(tzinfo=None)
            return '上午' if dt.hour < 12 else '下午'
        except Exception:
            return None

    @staticmethod
    def to_naive(dt) -> Optional[datetime]:
        """将存储的日期时间统一为北京时间naive值，用于天数差计算"""
        if dt is None:
            return None
        try:
            if isinstance(dt, str):
                dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
            if dt.tzinfo:
                dt = dt.astimezone(TimeConfig.BEIJING_TZ).replace(tzinfo=None)
            return dt
        except Exception:
            return None

    def should_send_return_reminder(self, user_id: str = "default_user", days_threshold: int = 30) -> bool:
        """判断是否应发送保养/回访提醒（距上次报修超过阈值天数）"""
        try:
            preferences = self.analyze_user_preferences(user_id)
            if not preferences or preferences['total_repairs'] < 1:
                return False

            last_repair = self.to_naive(preferences['last_repair_date'])
            if not last_repair:
                return False

            days_since_last = (TimeConfig.naive_now() - last_repair).days
            return days_since_last >= days_threshold

        except Exception as e:
            self.logger.error(f"判断回访提醒失败: {str(e)}")
            return False

    def generate_return_message(self, user_id: str = "default_user") -> Optional[str]:
        """生成保养/回访类消息（不依赖LLM的降级路径）"""
        try:
            preferences = self.analyze_user_preferences(user_id)
            if not preferences:
                return "您好！如需家电检修或保养，欢迎随时联系安居家电，工程师可为您安排上门服务。"

            tech_id = preferences.get('favorite_engineer_id')
            product = preferences.get('favorite_product_type')

            if tech_id:
                tech_name = self._get_engineer_name(tech_id) or '您熟悉的工程师'
                if product:
                    message = (f"您好！上次为您上门服务的{tech_name}工程师近期有空档，"
                               f"如您家的{product}需要复查或保养，可以为您安排。")
                else:
                    message = f"您好！{tech_name}工程师近期有空档，如需家电检修保养，可以为您安排上门。"
                message += "要不要帮您预约一个上门时间？"
            else:
                if product:
                    message = (f"您好！您家的{product}如需定期保养或故障复查，"
                               f"欢迎随时联系我们，工程师可安排时间上门服务。")
                else:
                    message = "您好！如需家电检修或保养，欢迎随时联系安居家电，工程师可为您安排上门服务。"

            return message

        except Exception as e:
            self.logger.error(f"生成回访消息失败: {str(e)}")
            return "您好！如需家电检修或保养，欢迎随时联系安居家电，工程师可为您安排上门服务。"

    def _get_engineer_name(self, engineer_id) -> Optional[str]:
        """查询工程师姓名"""
        try:
            from db import EngineerDBRouter
            db = EngineerDBRouter()
            tech_info = db.get_engineer_by_id(engineer_id)
            return tech_info.get('name') if tech_info else None
        except Exception:
            return None
