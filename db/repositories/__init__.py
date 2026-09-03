"""
Repositories Module

数据访问对象模块，包含：
- 工程师数据仓库
- 知识库数据仓库
- 用户行为数据仓库
- 报修工单数据仓库
- 订单数据仓库
- 转人工记录数据仓库
"""

from .engineer_repository import EngineerRepository
from .knowledge_repository import KnowledgeRepository
from .user_behavior_repository import UserBehaviorRepository
from .ticket_repository import TicketRepository
from .order_repository import OrderRepository
from .handover_repository import HumanHandoverRepository

__all__ = [
    'EngineerRepository',
    'KnowledgeRepository',
    'UserBehaviorRepository',
    'TicketRepository',
    'OrderRepository',
    'HumanHandoverRepository'
]
