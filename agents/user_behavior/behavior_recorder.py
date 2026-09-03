"""
行为记录器 - 负责记录客户的行为数据

职责：
1. 记录客户的操作行为（报修、咨询等）
2. 存储行为的上下文信息（时间、工程师、产品等）
3. 维护行为数据的完整性和一致性
4. 提供行为数据的查询接口
"""

from typing import Dict, Any, Optional, List
import logging


class BehaviorRecorder:
    """行为记录器 - 负责客户行为的记录和存储"""

    def __init__(self, behavior_service=None):
        """
        初始化行为记录器

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

    @staticmethod
    def _normalize_engineer_id(engineer_id) -> Optional[int]:
        """将工程师ID规范化为整数"""
        if engineer_id is None:
            return None
        try:
            return int(engineer_id)
        except (TypeError, ValueError):
            return None

    def record_behavior(self, action_type: str, action_data: Dict[str, Any] = None,
                        engineer_id=None, session_id: str = None,
                        user_id: str = "default_user") -> Optional[int]:
        """
        记录用户行为

        Args:
            action_type: 行为类型 (repair, consultation等)
            action_data: 行为相关数据
            engineer_id: 工程师ID
            session_id: 会话ID
            user_id: 用户ID（客户手机号或default_user）

        Returns:
            int: 行为记录ID，失败时返回None
        """
        try:
            engineer_id = self._normalize_engineer_id(engineer_id)

            # 优先使用Services层
            if self.behavior_service:
                success = self.behavior_service.record_behavior(
                    user_id=user_id,
                    action_type=action_type,
                    action_data=action_data,
                    engineer_id=engineer_id,
                    session_id=session_id or "default_session"
                )
                return 1 if success else None

            # 向后兼容：直接使用数据库
            return self.behavior_db.record_behavior(
                user_id=user_id,
                action_type=action_type,
                action_data=action_data,
                engineer_id=engineer_id,
                session_id=session_id
            )

        except Exception as e:
            self.logger.error(f"记录用户行为失败: {str(e)}")
            return None

    def record_repair_behavior(self, repair_data: Dict[str, Any],
                               engineer_id: int = None, session_id: str = None,
                               user_id: str = "default_user") -> Optional[int]:
        """
        记录报修行为的便捷方法

        Args:
            repair_data: 报修相关数据（product_type/fault_desc/address/start_time等）
            engineer_id: 上门工程师ID
            session_id: 会话ID
            user_id: 用户ID（客户手机号）

        Returns:
            int: 行为记录ID
        """
        return self.record_behavior(
            action_type='repair',
            action_data=repair_data,
            engineer_id=engineer_id,
            session_id=session_id,
            user_id=user_id
        )

    def record_consultation_behavior(self, consultation_data: Dict[str, Any],
                                     session_id: str = None,
                                     user_id: str = "default_user") -> Optional[int]:
        """
        记录咨询行为的便捷方法

        Args:
            consultation_data: 咨询相关数据
            session_id: 会话ID
            user_id: 用户ID（客户手机号）

        Returns:
            int: 行为记录ID
        """
        return self.record_behavior(
            action_type='consultation',
            action_data=consultation_data,
            session_id=session_id,
            user_id=user_id
        )

    def get_user_behaviors(self, action_type: str = None,
                           days_back: int = 30,
                           user_id: str = "default_user") -> List[Dict[str, Any]]:
        """
        获取用户行为记录

        Args:
            action_type: 行为类型过滤，None表示获取所有类型
            days_back: 获取多少天内的记录
            user_id: 用户ID

        Returns:
            List[Dict]: 行为记录列表
        """
        try:
            return self.behavior_db.get_user_behaviors(
                user_id=user_id,
                action_type=action_type,
                days_back=days_back
            )
        except Exception as e:
            self.logger.error(f"获取用户行为记录失败: {str(e)}")
            return []

    def get_behavior_statistics(self, days_back: int = 30,
                                user_id: str = "default_user") -> Dict[str, Any]:
        """
        获取行为统计信息

        Args:
            days_back: 统计天数
            user_id: 用户ID

        Returns:
            Dict: 统计信息
        """
        try:
            return self.behavior_db.get_user_statistics(
                user_id=user_id,
                days_back=days_back
            )
        except Exception as e:
            self.logger.error(f"获取行为统计失败: {str(e)}")
            return {}

    def delete_old_behaviors(self, days_to_keep: int = 90) -> int:
        """
        删除旧的行为记录

        Args:
            days_to_keep: 保留多少天的记录

        Returns:
            int: 删除的记录数量
        """
        try:
            # 行为记录保留策略：当前版本暂不物理删除，交由数据库保留策略处理
            self.logger.info(f"行为记录保留 {days_to_keep} 天策略生效，暂无自动清理")
            return 0
        except Exception as e:
            self.logger.error(f"清理旧行为记录失败: {str(e)}")
            return 0

    def validate_behavior_data(self, action_type: str, action_data: Dict[str, Any]) -> bool:
        """
        验证行为数据的有效性

        Args:
            action_type: 行为类型
            action_data: 行为数据

        Returns:
            bool: 数据是否有效
        """
        if not action_type or not action_data:
            return False

        if action_type == 'repair':
            required_fields = ['product_type', 'fault_desc', 'start_time']
            return all(field in action_data for field in required_fields)
        elif action_type == 'consultation':
            return 'query' in action_data or 'question' in action_data

        return True  # 其他类型默认有效
