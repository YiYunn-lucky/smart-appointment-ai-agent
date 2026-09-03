"""
偏好管理器 - 负责管理客户的偏好数据

职责：
1. 从报修数据中提取和更新客户偏好
2. 管理工程师偏好、上门时段偏好、产品/故障偏好等
3. 提供偏好数据的查询和统计
4. 处理偏好的变化和趋势分析

偏好键约定：
- engineer_id: 偏好工程师
- time_period: 常用上门时段（上午/下午）
- product_type: 常用产品品类
- fault_type: 常见故障
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
import logging


class PreferenceManager:
    """偏好管理器 - 负责客户偏好的管理和分析"""

    PREFERENCE_KEYS = ['engineer_id', 'time_period', 'product_type', 'fault_type']

    def __init__(self, behavior_service=None):
        """
        初始化偏好管理器

        Args:
            behavior_service: 用户行为服务实例
        """
        self.behavior_service = behavior_service
        self.logger = logging.getLogger(__name__)

    @property
    def behavior_db(self):
        """为了向后兼容，提供behavior_db属性（转发到行为仓库）"""
        if hasattr(self, 'behavior_service') and self.behavior_service:
            return self.behavior_service.user_behavior_repo
        return None

    def _update_preference(self, user_id: str, preference_type: str, preference_value: str) -> bool:
        """写入偏好（服务层优先，仓库兜底）"""
        try:
            if self.behavior_service:
                return self.behavior_service.update_user_preference(
                    user_id, preference_type, preference_value
                )
            return self.behavior_db.update_user_preference(
                user_id, preference_type, preference_value
            )
        except Exception as e:
            self.logger.error(f"更新偏好失败[{preference_type}]: {str(e)}")
            return False

    def update_preferences_from_repair(self, user_id: str, action_data: Dict[str, Any],
                                       engineer_id=None):
        """
        从报修数据中更新客户偏好

        Args:
            user_id: 用户ID（客户手机号）
            action_data: 报修行为数据
            engineer_id: 上门工程师ID
        """
        try:
            if engineer_id is not None:
                self._update_preference(user_id, 'engineer_id', str(engineer_id))

            if action_data.get('start_time'):
                self._update_time_preference(user_id, action_data['start_time'])

            if action_data.get('product_type'):
                self._update_preference(user_id, 'product_type', str(action_data['product_type']))

            if action_data.get('fault_desc'):
                self._update_preference(user_id, 'fault_type', str(action_data['fault_desc']))

        except Exception as e:
            self.logger.error(f"从报修数据更新用户偏好失败: {str(e)}")

    def _update_time_preference(self, user_id: str, start_time) -> bool:
        """更新常用上门时段偏好（9:00-18:00 内分上午/下午）"""
        try:
            if isinstance(start_time, str):
                start_datetime = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            else:
                start_datetime = start_time

            hour = start_datetime.hour
            time_period = '上午' if hour < 12 else '下午'

            return self._update_preference(user_id, 'time_period', time_period)
        except Exception as e:
            self.logger.error(f"更新时段偏好失败: {str(e)}")
            return False

    def update_engineer_preference(self, user_id: str, engineer_id) -> bool:
        """更新偏好工程师"""
        return self._update_preference(user_id, 'engineer_id', str(engineer_id))

    def update_time_preference(self, user_id: str, start_time) -> bool:
        """更新常用上门时段"""
        return self._update_time_preference(user_id, start_time)

    def update_product_preference(self, user_id: str, product_type: str) -> bool:
        """更新常用产品品类偏好"""
        return self._update_preference(user_id, 'product_type', product_type)

    def update_fault_preference(self, user_id: str, fault_desc: str) -> bool:
        """更新常见故障偏好"""
        return self._update_preference(user_id, 'fault_type', fault_desc)

    def get_user_preferences(self, user_id: str,
                             preference_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        获取用户偏好列表

        Returns:
            List[Dict]: 偏好记录列表（含confidence_score）
        """
        try:
            if self.behavior_service:
                return self.behavior_service.get_user_preferences(user_id) if not preference_type \
                    else [p for p in self.behavior_service.get_user_preferences(user_id)
                          if p.get('preference_type') == preference_type]
            return self.behavior_db.get_user_preferences(user_id, preference_type)
        except Exception as e:
            self.logger.error(f"获取用户偏好失败: {str(e)}")
            return []

    def _get_preference_value(self, user_id: str, preference_type: str) -> Optional[str]:
        """按类型取置信度最高的偏好值"""
        try:
            preferences = self.get_user_preferences(user_id)
            for preference in preferences:
                if preference.get('preference_type') == preference_type:
                    return preference.get('preference_value')
            return None
        except Exception:
            return None

    def get_preferred_engineer_id(self, user_id: str = "default_user") -> Optional[int]:
        """获取偏好的工程师ID"""
        try:
            value = self._get_preference_value(user_id, 'engineer_id')
            return int(value) if value else None
        except Exception as e:
            self.logger.error(f"获取偏好工程师ID失败: {str(e)}")
            return None

    def get_preferred_time_period(self, user_id: str = "default_user") -> Optional[str]:
        """获取偏好的上门时段（上午/下午）"""
        return self._get_preference_value(user_id, 'time_period')

    def get_preferred_product_type(self, user_id: str = "default_user") -> Optional[str]:
        """获取偏好的产品品类"""
        return self._get_preference_value(user_id, 'product_type')

    def get_preference_summary(self, user_id: str = "default_user") -> Dict[str, Any]:
        """
        获取偏好摘要信息

        Returns:
            Dict: 偏好摘要
        """
        try:
            preferences = self.get_user_preferences(user_id)
            preference_map = {
                p['preference_type']: p['preference_value']
                for p in preferences if p.get('preference_value')
            }

            summary = {
                'has_engineer_preference': bool(preference_map.get('engineer_id')),
                'has_time_preference': bool(preference_map.get('time_period')),
                'has_product_preference': bool(preference_map.get('product_type')),
                'has_fault_preference': bool(preference_map.get('fault_type')),
                'preference_count': len(preference_map)
            }

            if preference_map.get('engineer_id'):
                summary['preferred_engineer_id'] = int(preference_map['engineer_id'])
            if preference_map.get('time_period'):
                summary['preferred_time_period'] = preference_map['time_period']
            if preference_map.get('product_type'):
                summary['preferred_product_type'] = preference_map['product_type']
            if preference_map.get('fault_type'):
                summary['preferred_fault_type'] = preference_map['fault_type']

            return summary

        except Exception as e:
            self.logger.error(f"获取偏好摘要失败: {str(e)}")
            return {}

    def clear_preference(self, user_id: str, preference_type: str) -> bool:
        """清除特定类型的偏好"""
        return self._update_preference(user_id, preference_type, None)

    def clear_all_preferences(self, user_id: str) -> bool:
        """清除所有偏好"""
        try:
            for pref_type in self.PREFERENCE_KEYS:
                self.clear_preference(user_id, pref_type)
            self.logger.info(f"清除用户[{user_id}]所有偏好")
            return True
        except Exception as e:
            self.logger.error(f"清除所有偏好失败: {str(e)}")
            return False
