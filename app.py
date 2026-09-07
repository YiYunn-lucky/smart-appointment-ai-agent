"""
FastAPI应用程序

主应用程序入口，配置中间件、路由和异常处理
自动初始化知识库、工程师和演示订单数据
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from services.knowledge_service import KnowledgeService
from services.engineer_service import EngineerService
from services.recommendation_service import RecommendationService
from typing import List, Optional
import logging

# 导入路由
from api import api_routers
from api.core.exceptions import api_exception_handler, general_exception_handler, BusinessException
from web import router as web_router

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Pydantic模型
from pydantic import BaseModel

class KnowledgeRequest(BaseModel):
    content: str
    category: str
    keywords: List[str] = []

class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    category: Optional[str] = None

async def initialize_system():
    """系统启动时自动初始化（各模块独立容错，缺少 Embedding Key 时也能启动）"""
    logger.info("🚀 正在初始化安居家电售后智能客服系统...")

    # 初始化知识库服务
    try:
        logger.info("📚 初始化知识库服务...")
        knowledge_service = KnowledgeService()
        await knowledge_service.initialize()
    except Exception as e:
        logger.error(f"⚠️ 知识库初始化失败，配置 Embedding Key 后重启可恢复: {e}")

    # 初始化工程师数据
    try:
        logger.info("🧑‍🔧 初始化工程师数据...")
        engineer_service = EngineerService()
        engineer_service.initialize_default_engineers()
    except Exception as e:
        logger.error(f"⚠️ 工程师数据初始化失败: {e}")

    # 初始化演示订单（保修查询演示数据）
    try:
        logger.info("🧾 初始化演示订单数据...")
        from services.order_service import OrderService
        order_service = OrderService()
        order_service.initialize_default_orders()
    except Exception as e:
        logger.error(f"⚠️ 演示订单初始化失败: {e}")

    # 启动售后提醒调度服务
    try:
        logger.info("🎯 启动售后提醒调度服务...")
        recommendation_service = RecommendationService()
        if recommendation_service.start_scheduler():
            logger.info("✅ 售后提醒调度服务启动成功")
        else:
            logger.warning("⚠️ 售后提醒调度服务启动失败")
    except Exception as e:
        logger.error(f"⚠️ 售后提醒调度服务异常: {e}")

    # 启动 AutoDream 离线沉淀调度服务（回放老客户行为，沉淀可召回画像；沉淀结果落审计）
    try:
        logger.info("🌙 启动 AutoDream 离线沉淀调度服务...")
        from services.dream_service import DreamService
        from services.audit_service import AuditService

        audit_logger = AuditService().record
        dream_service = DreamService(audit_logger=audit_logger, audit_actor='dream_scheduler')
        if dream_service.start_scheduler():
            logger.info("✅ AutoDream 离线沉淀调度服务启动成功")
        else:
            logger.warning("⚠️ AutoDream 离线沉淀调度服务启动失败")
    except Exception as e:
        logger.error(f"⚠️ AutoDream 离线沉淀调度服务异常: {e}")

    logger.info("✅ 系统初始化完成！")

def create_app() -> FastAPI:
    """创建FastAPI应用实例"""
    
    app = FastAPI(
        title="安居家电售后智能客服系统",
        description="提供家电报修预约、售后咨询、订单保修查询、投诉转人工等功能的API服务",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc"
    )

    # 添加CORS中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 生产环境中应该设置具体的域名
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册异常处理器
    app.add_exception_handler(BusinessException, api_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)

    # 注册API路由
    for router in api_routers:
        app.include_router(router)

    # 注册Web界面路由
    app.include_router(web_router)

    # 静态文件
    app.mount("/static", StaticFiles(directory="web/static"), name="static")

    # 添加启动事件
    @app.on_event("startup")
    async def startup_event():
        """应用启动时自动初始化系统"""
        await initialize_system()

    return app

# 创建应用实例
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
