"""
Database Module

数据库模块，包含：
- 数据模型定义
- 数据访问对象 (Repository)
- 数据库路由器
- 会话管理
"""

from .db_router import DatabaseRouter, EngineerDBRouter, KnowledgeDBRouter, UserBehaviorDBRouter
from .repositories import (
    EngineerRepository,
    KnowledgeRepository,
    UserBehaviorRepository,
    TicketRepository,
    OrderRepository,
    HumanHandoverRepository,
    ChatSessionRepository,
    UserMemoryRepository,
)
from .base import SessionManager
from .models import (
    Base, Engineer, EngineerSchedule, RepairTicket, Order, HumanHandover,
    KnowledgeDocument, UserBehavior, UserPreference, UserRecommendation,
    ChatSession, UserMemory
)

__all__ = [
    # 主要入口
    'DatabaseRouter',

    # 兼容性路由器
    'EngineerDBRouter',
    'KnowledgeDBRouter',
    'UserBehaviorDBRouter',

    # Repository模式
    'EngineerRepository',
    'KnowledgeRepository',
    'UserBehaviorRepository',
    'TicketRepository',
    'OrderRepository',
    'HumanHandoverRepository',
    'ChatSessionRepository',
    'UserMemoryRepository',

    # 基础设施
    'SessionManager',

    # 数据模型
    'Base',
    'Engineer',
    'EngineerSchedule',
    'RepairTicket',
    'Order',
    'HumanHandover',
    'KnowledgeDocument',
    'UserBehavior',
    'UserPreference',
    'UserRecommendation',
    'ChatSession',
    'UserMemory'
]
