from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from ..base.interfaces import BaseUserBehaviorRepository
from ..base.session_manager import SessionManager
from ..models import UserBehavior, UserPreference, UserRecommendation, Engineer


class UserBehaviorRepository(BaseUserBehaviorRepository):
    """
    用户行为数据访问对象
    
    职责：
    1. 用户行为记录和查询
    2. 用户偏好管理
    3. 推荐系统数据支持
    4. 用户统计信息生成
    """
    
    def __init__(self, session_manager: SessionManager):
        """
        初始化用户行为数据仓库
        
        Args:
            session_manager: 会话管理器
        """
        self.session_manager = session_manager

    def record_behavior(self, user_id: str, action_type: str, action_data: Optional[Dict[str, Any]] = None, 
                       engineer_id: Optional[int] = None, session_id: Optional[str] = None) -> int:
        """
        记录用户行为
        
        Args:
            user_id: 用户ID
            action_type: 行为类型
            action_data: 行为数据
            engineer_id: 工程师ID
            session_id: 会话ID
            
        Returns:
            新创建的行为记录ID
        """
        with self.session_manager.session_scope() as session:
            behavior = UserBehavior(
                user_id=user_id,
                action_type=action_type,
                action_data=action_data,
                engineer_id=engineer_id,
                session_id=session_id
            )
            session.add(behavior)
            session.flush()
            return behavior.id

    def get_user_behaviors(self, user_id: str, action_type: Optional[str] = None, 
                          days_back: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        获取用户行为历史
        
        Args:
            user_id: 用户ID
            action_type: 行为类型过滤
            days_back: 查询多少天内的记录
            
        Returns:
            用户行为列表
        """
        with self.session_manager.session_scope() as session:
            query = session.query(UserBehavior).filter(UserBehavior.user_id == user_id)
            
            if action_type:
                query = query.filter(UserBehavior.action_type == action_type)
            
            if days_back:
                cutoff_date = datetime.utcnow() - timedelta(days=days_back)
                query = query.filter(UserBehavior.created_at >= cutoff_date)
            
            behaviors = query.order_by(UserBehavior.created_at.desc()).all()
            
            return [self._behavior_to_dict(behavior) for behavior in behaviors]

    def get_recent_behaviors(self, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取用户最近的行为记录
        
        Args:
            user_id: 用户ID
            limit: 返回记录数量限制
            
        Returns:
            最近的行为记录列表
        """
        with self.session_manager.session_scope() as session:
            behaviors = session.query(UserBehavior).filter(
                UserBehavior.user_id == user_id
            ).order_by(UserBehavior.created_at.desc()).limit(limit).all()
            
            return [self._behavior_to_dict(behavior) for behavior in behaviors]

    def update_user_preference(self, user_id: str, preference_type: str, preference_value: str) -> bool:
        """
        更新用户偏好
        
        Args:
            user_id: 用户ID
            preference_type: 偏好类型
            preference_value: 偏好值
            
        Returns:
            更新是否成功
        """
        with self.session_manager.session_scope() as session:
            # 查找现有偏好
            existing = session.query(UserPreference).filter(
                UserPreference.user_id == user_id,
                UserPreference.preference_type == preference_type,
                UserPreference.preference_value == preference_value
            ).first()
            
            if existing:
                # 增加置信度
                existing.confidence_score += 1
                existing.last_updated = datetime.utcnow()
            else:
                # 创建新偏好
                preference = UserPreference(
                    user_id=user_id,
                    preference_type=preference_type,
                    preference_value=preference_value,
                    confidence_score=1
                )
                session.add(preference)
            
            return True

    def get_user_preferences(self, user_id: str, preference_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        获取用户偏好
        
        Args:
            user_id: 用户ID
            preference_type: 偏好类型过滤
            
        Returns:
            用户偏好列表
        """
        with self.session_manager.session_scope() as session:
            query = session.query(UserPreference).filter(UserPreference.user_id == user_id)
            
            if preference_type:
                query = query.filter(UserPreference.preference_type == preference_type)
            
            preferences = query.order_by(UserPreference.confidence_score.desc()).all()
            
            return [self._preference_to_dict(preference) for preference in preferences]

    def get_top_preferences(self, user_id: str, preference_type: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        获取用户特定类型的高置信度偏好
        
        Args:
            user_id: 用户ID
            preference_type: 偏好类型
            limit: 返回数量限制
            
        Returns:
            高置信度偏好列表
        """
        with self.session_manager.session_scope() as session:
            preferences = session.query(UserPreference).filter(
                UserPreference.user_id == user_id,
                UserPreference.preference_type == preference_type
            ).order_by(UserPreference.confidence_score.desc()).limit(limit).all()
            
            return [self._preference_to_dict(preference) for preference in preferences]

    def create_recommendation(self, user_id: str, recommendation_type: str, content: str, 
                            engineer_id: Optional[int] = None) -> int:
        """
        创建推荐
        
        Args:
            user_id: 用户ID
            recommendation_type: 推荐类型
            content: 推荐内容
            engineer_id: 相关工程师ID
            
        Returns:
            新创建的推荐ID
        """
        with self.session_manager.session_scope() as session:
            recommendation = UserRecommendation(
                user_id=user_id,
                recommendation_type=recommendation_type,
                content=content,
                engineer_id=engineer_id
            )
            session.add(recommendation)
            session.flush()
            return recommendation.id

    def get_pending_recommendations(self, user_id: str) -> List[Dict[str, Any]]:
        """
        获取待发送的推荐
        
        Args:
            user_id: 用户ID
            
        Returns:
            待发送推荐列表
        """
        with self.session_manager.session_scope() as session:
            recommendations = session.query(UserRecommendation).filter(
                UserRecommendation.user_id == user_id,
                UserRecommendation.is_sent == 0
            ).order_by(UserRecommendation.created_at.desc()).all()
            
            return [self._recommendation_to_dict(recommendation) for recommendation in recommendations]

    def mark_recommendation_sent(self, recommendation_id: int) -> bool:
        """
        标记推荐为已发送
        
        Args:
            recommendation_id: 推荐ID
            
        Returns:
            标记是否成功
        """
        with self.session_manager.session_scope() as session:
            recommendation = session.query(UserRecommendation).filter(
                UserRecommendation.id == recommendation_id
            ).first()
            
            if recommendation:
                recommendation.is_sent = 1
                recommendation.sent_at = datetime.utcnow()
                return True
            return False

    def get_user_statistics(self, user_id: str, days_back: int = 30) -> Dict[str, Any]:
        """
        获取用户统计信息

        Args:
            user_id: 用户ID
            days_back: 统计天数

        Returns:
            用户统计信息字典
        """
        with self.session_manager.session_scope() as session:
            cutoff_date = datetime.utcnow() - timedelta(days=days_back)

            # 总行为数
            total_behaviors = session.query(UserBehavior).filter(
                UserBehavior.user_id == user_id,
                UserBehavior.created_at >= cutoff_date
            ).count()

            # 报修次数
            repair_count = session.query(UserBehavior).filter(
                UserBehavior.user_id == user_id,
                UserBehavior.action_type == 'repair',
                UserBehavior.created_at >= cutoff_date
            ).count()

            # 咨询次数
            consultation_count = session.query(UserBehavior).filter(
                UserBehavior.user_id == user_id,
                UserBehavior.action_type == 'consultation',
                UserBehavior.created_at >= cutoff_date
            ).count()

            # 最喜欢的工程师
            from sqlalchemy import func
            favorite_engineer = session.query(
                UserBehavior.engineer_id,
                Engineer.name,
                func.count(UserBehavior.engineer_id).label('count')
            ).join(Engineer).filter(
                UserBehavior.user_id == user_id,
                UserBehavior.action_type == 'repair',
                UserBehavior.created_at >= cutoff_date
            ).group_by(UserBehavior.engineer_id, Engineer.name).order_by(
                func.count(UserBehavior.engineer_id).desc()
            ).first()

            # 最后一次报修
            last_repair = session.query(UserBehavior).filter(
                UserBehavior.user_id == user_id,
                UserBehavior.action_type == 'repair'
            ).order_by(UserBehavior.created_at.desc()).first()

            return {
                'total_behaviors': total_behaviors,
                'repair_count': repair_count,
                'consultation_count': consultation_count,
                'favorite_engineer_id': favorite_engineer[0] if favorite_engineer else None,
                'favorite_engineer_name': favorite_engineer[1] if favorite_engineer else None,
                'favorite_engineer_visits': favorite_engineer[2] if favorite_engineer else 0,
                'last_repair_date': last_repair.created_at if last_repair else None,
                'days_since_last_repair': (datetime.utcnow() - last_repair.created_at).days if last_repair else None,
                'period_days': days_back
            }

    def get_engineer_popularity(self, days_back: int = 30) -> List[Dict[str, Any]]:
        """
        获取工程师服务量统计（按报修单量排名）

        Args:
            days_back: 统计天数

        Returns:
            工程师服务量列表
        """
        with self.session_manager.session_scope() as session:
            cutoff_date = datetime.utcnow() - timedelta(days=days_back)

            from sqlalchemy import func

            popularity = session.query(
                UserBehavior.engineer_id,
                Engineer.name,
                func.count(UserBehavior.engineer_id).label('repair_count'),
                func.count(func.distinct(UserBehavior.user_id)).label('unique_users')
            ).join(Engineer).filter(
                UserBehavior.action_type == 'repair',
                UserBehavior.created_at >= cutoff_date
            ).group_by(UserBehavior.engineer_id, Engineer.name).order_by(
                func.count(UserBehavior.engineer_id).desc()
            ).all()

            return [
                {
                    'engineer_id': p[0],
                    'engineer_name': p[1],
                    'repair_count': p[2],
                    'unique_users': p[3]
                }
                for p in popularity
            ]

    def list_user_ids(self, limit: int = 1000) -> List[str]:
        """列出有行为记录的客户ID（排除占位符；供 AutoDream 离线沉淀扫描）"""
        with self.session_manager.session_scope() as session:
            rows = session.query(UserBehavior.user_id).filter(
                UserBehavior.user_id.notin_(['guest', 'default_user', ''])
            ).distinct().limit(limit).all()
            return [r[0] for r in rows]

    def decay_preference_confidence(self, preference_id: int) -> bool:
        """偏好冲突降权：置信度减半（下限 1）——持续未被再次确认的旧偏好逐渐淡出"""
        with self.session_manager.session_scope() as session:
            row = session.query(UserPreference).filter(
                UserPreference.id == preference_id
            ).first()
            if row is None:
                return False
            row.confidence_score = max(1, (row.confidence_score or 1) // 2)
            row.last_updated = datetime.utcnow()
            return True

    def _behavior_to_dict(self, behavior: UserBehavior) -> Dict[str, Any]:
        """将行为对象转换为字典"""
        return {
            'id': behavior.id,
            'user_id': behavior.user_id,
            'action_type': behavior.action_type,
            'action_data': behavior.action_data,
            'engineer_id': behavior.engineer_id,
            'engineer_name': behavior.engineer.name if behavior.engineer else None,
            'session_id': behavior.session_id,
            'created_at': behavior.created_at
        }

    def _preference_to_dict(self, preference: UserPreference) -> Dict[str, Any]:
        """将偏好对象转换为字典"""
        return {
            'id': preference.id,
            'user_id': preference.user_id,
            'preference_type': preference.preference_type,
            'preference_value': preference.preference_value,
            'confidence_score': preference.confidence_score,
            'last_updated': preference.last_updated
        }

    def _recommendation_to_dict(self, recommendation: UserRecommendation) -> Dict[str, Any]:
        """将推荐对象转换为字典"""
        return {
            'id': recommendation.id,
            'user_id': recommendation.user_id,
            'recommendation_type': recommendation.recommendation_type,
            'content': recommendation.content,
            'engineer_id': recommendation.engineer_id,
            'engineer_name': recommendation.engineer.name if recommendation.engineer else None,
            'is_sent': bool(recommendation.is_sent),
            'created_at': recommendation.created_at,
            'sent_at': recommendation.sent_at
        }
