"""
API 路由模块

汇总各业务子模块的 APIRouter 并统一导出，供 app.py 注册。
"""

# 导入各业务模块的路由
from .appointment import router as appointment_router
from .consultation import router as consultation_router
from .task import router as task_router
from .knowledge import router as knowledge_router
from .engineer import router as engineer_router
from .ticket import router as ticket_router
from .user_behavior_analysis import router as follow_up_router
from .audit import router as audit_router

# 创建API路由列表（用于注册到FastAPI应用）
api_routers = [
    appointment_router,
    consultation_router,
    task_router,
    knowledge_router,
    engineer_router,
    ticket_router,
    follow_up_router,
    audit_router
]
