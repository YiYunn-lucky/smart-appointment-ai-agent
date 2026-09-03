from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime


class BaseEngineerRepository(ABC):
    """
    工程师数据访问抽象接口

    定义工程师（上门维修人员）相关的所有数据操作方法
    """

    @abstractmethod
    def add_engineer(self, name: str, skills: Optional[str] = None, service_region: Optional[str] = None) -> int:
        """添加工程师"""
        pass

    @abstractmethod
    def get_engineer_by_id(self, engineer_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取工程师信息"""
        pass

    @abstractmethod
    def get_engineer_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """根据姓名获取工程师信息"""
        pass

    @abstractmethod
    def get_all_engineers(self) -> List[Dict[str, Any]]:
        """获取所有工程师"""
        pass

    @abstractmethod
    def get_all_skills(self) -> List[str]:
        """获取所有工程师的品类专长文本"""
        pass

    @abstractmethod
    def update_engineer(self, engineer_id: int, **updates) -> bool:
        """更新工程师信息"""
        pass

    @abstractmethod
    def delete_engineer(self, engineer_id: int) -> bool:
        """删除工程师"""
        pass

    @abstractmethod
    def get_engineers_by_region(self, service_region: str) -> List[Dict[str, Any]]:
        """按服务区域获取工程师"""
        pass


class BaseScheduleRepository(ABC):
    """
    排班数据访问抽象接口

    定义排班（忙闲档期）相关的所有数据操作方法
    """

    @abstractmethod
    def add_schedule(self, engineer_id: int, start_time: datetime, end_time: datetime,
                    status: str, ticket_id: Optional[int] = None) -> int:
        """添加排班"""
        pass

    @abstractmethod
    def get_engineer_schedules(self, engineer_id: int, date: datetime) -> List[Dict[str, Any]]:
        """获取工程师指定日期的排班"""
        pass

    @abstractmethod
    def is_engineer_available(self, engineer_id: int, start_time: datetime, end_time: datetime) -> bool:
        """检查工程师时间段是否可用"""
        pass

    @abstractmethod
    def update_schedule_status(self, schedule_id: int, status: str, ticket_id: Optional[int] = None) -> bool:
        """更新排班状态"""
        pass

    @abstractmethod
    def release_schedule_by_ticket(self, ticket_id: int) -> bool:
        """根据工单释放对应的忙档排班"""
        pass

    @abstractmethod
    def delete_schedule(self, schedule_id: int) -> bool:
        """删除排班"""
        pass


class BaseRepairTicketRepository(ABC):
    """
    报修工单数据访问抽象接口
    """

    @abstractmethod
    def create_ticket(self, user_name: Optional[str], user_phone: str, product_type: str,
                      fault_desc: str, address: str, start_time: datetime, end_time: datetime,
                      engineer_id: Optional[int] = None, status: str = 'pending') -> int:
        """创建报修工单"""
        pass

    @abstractmethod
    def get_ticket_by_id(self, ticket_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取工单"""
        pass

    @abstractmethod
    def get_ticket_by_no(self, ticket_no: str) -> Optional[Dict[str, Any]]:
        """根据工单号获取工单"""
        pass

    @abstractmethod
    def get_tickets(self, status: Optional[str] = None, phone: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取工单列表，支持状态/手机号过滤"""
        pass

    @abstractmethod
    def update_ticket(self, ticket_id: int, **updates) -> bool:
        """更新工单（状态流转、派单等）"""
        pass

    @abstractmethod
    def generate_ticket_no(self) -> str:
        """生成工单号：AX + YYYYMMDD + 当日两位数序号"""
        pass


class BaseOrderRepository(ABC):
    """
    订单/保修数据访问抽象接口
    """

    @abstractmethod
    def create_order(self, user_phone: str, product_type: str, user_name: Optional[str] = None,
                     brand_model: Optional[str] = None, purchase_date: Optional[datetime] = None,
                     warranty_years: int = 3) -> int:
        """创建订单"""
        pass

    @abstractmethod
    def get_orders_by_phone(self, user_phone: str) -> List[Dict[str, Any]]:
        """按手机号查询订单"""
        pass

    @abstractmethod
    def get_all_orders(self) -> List[Dict[str, Any]]:
        """获取全部订单（按手机号去重后的演示数据/调度任务使用）"""
        pass


class BaseHumanHandoverRepository(ABC):
    """
    转人工记录数据访问抽象接口
    """

    @abstractmethod
    def create_handover(self, issue_summary: str, user_name: Optional[str] = None,
                        user_phone: Optional[str] = None, ticket_id: Optional[int] = None) -> int:
        """创建转人工记录"""
        pass

    @abstractmethod
    def get_handovers(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取转人工记录列表"""
        pass


class BaseKnowledgeRepository(ABC):
    """
    知识库数据访问抽象接口

    定义知识库相关的所有数据操作方法
    """

    @abstractmethod
    def add_document(self, content: str, category: str, keywords: Optional[List[str]] = None,
                    embedding: Optional[List[float]] = None) -> int:
        """添加知识文档"""
        pass

    @abstractmethod
    def get_document(self, doc_id: int) -> Optional[Dict[str, Any]]:
        """获取指定文档"""
        pass

    @abstractmethod
    def get_all_documents(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        """获取所有文档"""
        pass

    @abstractmethod
    def update_document(self, doc_id: int, content: Optional[str] = None, category: Optional[str] = None,
                       keywords: Optional[List[str]] = None, embedding: Optional[List[float]] = None) -> bool:
        """更新文档"""
        pass

    @abstractmethod
    def delete_document(self, doc_id: int, soft_delete: bool = True) -> bool:
        """删除文档（支持软删除）"""
        pass

    @abstractmethod
    def search_documents_by_category(self, category: str) -> List[Dict[str, Any]]:
        """按分类搜索文档"""
        pass

    @abstractmethod
    def search_documents_by_keywords(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """按关键词搜索文档"""
        pass

    @abstractmethod
    def get_all_categories(self) -> List[str]:
        """获取所有分类"""
        pass

    @abstractmethod
    def get_documents_count(self) -> int:
        """获取文档总数"""
        pass


class BaseUserBehaviorRepository(ABC):
    """
    用户行为数据访问抽象接口

    定义用户行为分析相关的所有数据操作方法
    """

    @abstractmethod
    def record_behavior(self, user_id: str, action_type: str, action_data: Optional[Dict[str, Any]] = None,
                       engineer_id: Optional[int] = None, session_id: Optional[str] = None) -> int:
        """记录用户行为"""
        pass

    @abstractmethod
    def get_user_behaviors(self, user_id: str, action_type: Optional[str] = None,
                          days_back: Optional[int] = None) -> List[Dict[str, Any]]:
        """获取用户行为历史"""
        pass

    @abstractmethod
    def update_user_preference(self, user_id: str, preference_type: str, preference_value: str) -> bool:
        """更新用户偏好"""
        pass

    @abstractmethod
    def get_user_preferences(self, user_id: str, preference_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取用户偏好"""
        pass

    @abstractmethod
    def create_recommendation(self, user_id: str, recommendation_type: str, content: str,
                            engineer_id: Optional[int] = None) -> int:
        """创建推荐"""
        pass

    @abstractmethod
    def get_pending_recommendations(self, user_id: str) -> List[Dict[str, Any]]:
        """获取待发送的推荐"""
        pass

    @abstractmethod
    def mark_recommendation_sent(self, recommendation_id: int) -> bool:
        """标记推荐为已发送"""
        pass

    @abstractmethod
    def get_user_statistics(self, user_id: str, days_back: int = 30) -> Dict[str, Any]:
        """获取用户统计信息"""
        pass
