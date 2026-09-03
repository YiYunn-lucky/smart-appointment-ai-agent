"""
业务服务层模块

包含：
- 知识库服务
- 工程师服务
- 报修工单服务
- 订单/保修服务
- 转人工服务
- 用户行为服务
- 推荐调度服务
- 文本嵌入工具
"""

from .text_embedding import (
    embed_input,
    find_best_match_indices,
    save_engineer_embeddings,
    load_engineer_embeddings
)
from .knowledge_service import KnowledgeService
from .engineer_service import EngineerService
from .ticket_service import TicketService
from .order_service import OrderService
from .handover_service import HandoverService
from .user_behavior_service import UserBehaviorService
from .recommendation_service import RecommendationService

__all__ = [
    'embed_input',
    'find_best_match_indices',
    'save_engineer_embeddings',
    'load_engineer_embeddings',
    'KnowledgeService',
    'EngineerService',
    'TicketService',
    'OrderService',
    'HandoverService',
    'UserBehaviorService',
    'RecommendationService'
]
