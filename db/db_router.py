from .base import SessionManager
from .repositories import (
    EngineerRepository,
    KnowledgeRepository,
    UserBehaviorRepository,
    TicketRepository,
    OrderRepository,
    HumanHandoverRepository,
    ChatSessionRepository,
    UserMemoryRepository,
    DreamCheckpointRepository,
    AuditLogRepository,
)


class DatabaseRouter:
    """
    数据库路由器

    职责：
    1. 管理数据库连接和会话
    2. 提供统一的数据访问入口
    3. 协调各个Repository的操作
    """

    def __init__(self, db_path: str = 'sqlite:///data/smart_appointment.db'):
        """
        初始化数据库路由器

        Args:
            db_path: 数据库连接路径
        """
        self.session_manager = SessionManager(db_path)

        # 初始化各个Repository
        self.engineer_repo = EngineerRepository(self.session_manager)
        self.knowledge_repo = KnowledgeRepository(self.session_manager)
        self.user_behavior_repo = UserBehaviorRepository(self.session_manager)
        self.ticket_repo = TicketRepository(self.session_manager)
        self.order_repo = OrderRepository(self.session_manager)
        self.handover_repo = HumanHandoverRepository(self.session_manager)
        self.chat_session_repo = ChatSessionRepository(self.session_manager)
        self.user_memory_repo = UserMemoryRepository(self.session_manager)
        self.dream_checkpoint_repo = DreamCheckpointRepository(self.session_manager)
        self.audit_log_repo = AuditLogRepository(self.session_manager)

    @property
    def chat_sessions(self) -> ChatSessionRepository:
        """获取聊天会话数据仓库"""
        return self.chat_session_repo

    @property
    def user_memories(self) -> UserMemoryRepository:
        """获取用户长期记忆数据仓库"""
        return self.user_memory_repo

    @property
    def dream_checkpoints(self) -> DreamCheckpointRepository:
        """获取 AutoDream 沉淀检查点数据仓库"""
        return self.dream_checkpoint_repo

    @property
    def engineers(self) -> EngineerRepository:
        """获取工程师数据仓库"""
        return self.engineer_repo

    @property
    def knowledge(self) -> KnowledgeRepository:
        """获取知识库数据仓库"""
        return self.knowledge_repo

    @property
    def user_behavior(self) -> UserBehaviorRepository:
        """获取用户行为数据仓库"""
        return self.user_behavior_repo

    @property
    def tickets(self) -> TicketRepository:
        """获取报修工单数据仓库"""
        return self.ticket_repo

    @property
    def orders(self) -> OrderRepository:
        """获取订单数据仓库"""
        return self.order_repo

    @property
    def handovers(self) -> HumanHandoverRepository:
        """获取转人工记录数据仓库"""
        return self.handover_repo

    @property
    def audit_logs(self) -> AuditLogRepository:
        """获取操作审计日志数据仓库（M15）"""
        return self.audit_log_repo

    def close(self):
        """关闭数据库连接"""
        self.session_manager.close()


# 兼容层：为老调用方保留直接路由类
class EngineerDBRouter:
    """工程师数据库路由器（兼容性类）"""

    def __init__(self, db_type='local', **kwargs):
        self.db_router = DatabaseRouter(**kwargs)
        self.engineer_repo = self.db_router.engineers

    def add_engineer(self, name, skills=None, service_region=None) -> int:
        return self.engineer_repo.add_engineer(name, skills, service_region)

    def get_engineer_by_name(self, name: str):
        return self.engineer_repo.get_engineer_by_name(name)

    def get_engineer_by_id(self, engineer_id: int):
        return self.engineer_repo.get_engineer_by_id(engineer_id)

    def get_all_engineers(self):
        return self.engineer_repo.get_all_engineers()

    def get_all_skills(self):
        return self.engineer_repo.get_all_skills()

    # 排班相关方法
    def add_schedule(self, engineer_id: int, start_time, end_time, status, ticket_id=None) -> int:
        return self.engineer_repo.add_schedule(engineer_id, start_time, end_time, status, ticket_id)

    def get_engineer_schedules(self, engineer_id: int, date):
        return self.engineer_repo.get_engineer_schedules(engineer_id, date)

    def is_engineer_available(self, engineer_id: int, start_time, end_time) -> bool:
        return self.engineer_repo.is_engineer_available(engineer_id, start_time, end_time)

    def get_engineers_by_region(self, service_region: str):
        return self.engineer_repo.get_engineers_by_region(service_region)

    def release_schedule_by_ticket(self, ticket_id: int) -> bool:
        return self.engineer_repo.release_schedule_by_ticket(ticket_id)


class KnowledgeDBRouter:
    """知识库数据库路由器（兼容性类）"""

    def __init__(self, db_type='local', **kwargs):
        self.db_router = DatabaseRouter(**kwargs)
        self.knowledge_repo = self.db_router.knowledge

    def add_document(self, content: str, category: str, keywords=None, embedding=None) -> int:
        return self.knowledge_repo.add_document(content, category, keywords, embedding)

    def get_document(self, doc_id: int):
        return self.knowledge_repo.get_document(doc_id)

    def get_all_documents(self, include_inactive: bool = False):
        return self.knowledge_repo.get_all_documents(include_inactive)

    def update_document(self, doc_id: int, content=None, category=None, keywords=None, embedding=None) -> bool:
        return self.knowledge_repo.update_document(doc_id, content, category, keywords, embedding)

    def delete_document(self, doc_id: int, soft_delete: bool = True) -> bool:
        return self.knowledge_repo.delete_document(doc_id, soft_delete)

    def search_documents_by_category(self, category: str):
        return self.knowledge_repo.search_documents_by_category(category)

    def search_documents_by_keywords(self, keywords):
        return self.knowledge_repo.search_documents_by_keywords(keywords)

    def get_all_categories(self):
        return self.knowledge_repo.get_all_categories()

    def get_documents_count(self) -> int:
        return self.knowledge_repo.get_documents_count()


class UserBehaviorDBRouter:
    """用户行为数据库路由器（兼容性类）"""

    def __init__(self, db_type='local', **kwargs):
        self.db_router = DatabaseRouter(**kwargs)
        self.user_behavior_repo = self.db_router.user_behavior

    def record_behavior(self, user_id: str, action_type: str, action_data=None, engineer_id=None, session_id=None) -> int:
        return self.user_behavior_repo.record_behavior(user_id, action_type, action_data, engineer_id, session_id)

    def get_user_behaviors(self, user_id: str, action_type=None, days_back=None):
        return self.user_behavior_repo.get_user_behaviors(user_id, action_type, days_back)

    def get_user_preferences(self, user_id: str, preference_type=None):
        return self.user_behavior_repo.get_user_preferences(user_id, preference_type)

    def update_user_preference(self, user_id: str, preference_type: str, preference_value: str) -> bool:
        return self.user_behavior_repo.update_user_preference(user_id, preference_type, preference_value)

    def create_recommendation(self, user_id: str, recommendation_type: str, content: str, engineer_id=None) -> int:
        return self.user_behavior_repo.create_recommendation(user_id, recommendation_type, content, engineer_id)

    def get_pending_recommendations(self, user_id: str):
        return self.user_behavior_repo.get_pending_recommendations(user_id)

    def mark_recommendation_sent(self, recommendation_id: int) -> bool:
        return self.user_behavior_repo.mark_recommendation_sent(recommendation_id)

    def get_user_statistics(self, user_id: str, days_back: int = 30):
        return self.user_behavior_repo.get_user_statistics(user_id, days_back)
