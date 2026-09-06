"""
Web界面路由

处理前端页面渲染和聊天功能
"""
import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from api.chat_handler import ProcessUserInput_stream

# 创建logger实例
logger = logging.getLogger(__name__)
# 模板配置
templates = Jinja2Templates(directory="web/templates")

# Web路由器
router = APIRouter(tags=["Web界面"])

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None

@router.get("/", response_class=HTMLResponse, summary="主页")
async def read_root(request: Request):
    """渲染主页聊天界面"""
    return templates.TemplateResponse("index.html", {"request": request})

@router.post("/chat/stream", summary="流式聊天")
async def chat_stream_endpoint(chat: ChatRequest):
    """处理流式聊天请求（会话隔离：session_id 缺省时后端生成并通过响应头下发）"""
    session_id = chat.session_id or str(uuid.uuid4())

    async def token_generator():
        async for token in ProcessUserInput_stream(chat.message, session_id=session_id):
            yield token

    return StreamingResponse(
        token_generator(),
        media_type="text/plain",
        headers={"X-Session-Id": session_id},
    )

@router.post("/chat", summary="兼容性聊天接口")
async def chat_endpoint(chat: ChatRequest):
    """兼容性聊天接口，建议使用/chat/stream"""
    session_id = chat.session_id or str(uuid.uuid4())

    async def token_generator():
        async for token in ProcessUserInput_stream(chat.message, session_id=session_id):
            yield token

    return StreamingResponse(
        token_generator(),
        media_type="text/plain",
        headers={"X-Session-Id": session_id},
    )

@router.get("/knowledge", response_class=HTMLResponse, summary="知识库管理页面")
async def knowledge_page(request: Request):
    """知识库管理页面"""
    # 通过API层获取知识库数据
    try:
        from api.knowledge import get_all_knowledge
        
        # 调用API层函数获取数据
        knowledge_data = await get_all_knowledge()
        documents = knowledge_data.get("documents", [])
        categories = knowledge_data.get("categories", [])
        
        return templates.TemplateResponse("knowledge_management.html", {
            "request": request,
            "documents": documents,
            "categories": categories
        })
    except Exception as e:
        return templates.TemplateResponse("knowledge_management.html", {
            "request": request,
            "documents": [],
            "categories": [],
            "error": str(e)
        })

@router.get("/engineers", response_class=HTMLResponse, summary="工程师管理页面")
async def engineer_page(request: Request):
    """工程师管理页面（数据由前端 fetch /api/engineers 加载）"""
    return templates.TemplateResponse("engineers.html", {"request": request})

@router.get("/engineer_schedules", response_class=HTMLResponse, summary="工程师今日排班页面")
async def engineer_schedule_page(request: Request):
    """工程师今日排班页面"""
    try:
        from api.engineer import get_all_engineers_schedule_today
        from config.time_config import time_config

        # 获取当前日期
        current_date = time_config.current_date_str()

        # 通过API层获取所有工程师的排班数据
        schedules_data = await get_all_engineers_schedule_today()

        # 构建排班数据格式 - 直接使用API返回的数据
        schedule = []
        for schedule_item in schedules_data:
            schedule.append({
                "id": schedule_item["engineer_id"],
                "name": schedule_item["engineer_name"],
                "busy_periods": schedule_item["busy_periods"]
            })

        return templates.TemplateResponse("engineer_schedules.html", {
            "request": request,
            "schedule": schedule,
            "current_date": current_date
        })
    except Exception as e:
        logger.error(f"加载工程师排班数据失败: {str(e)}")
        return templates.TemplateResponse("engineer_schedules.html", {
            "request": request,
            "schedule": [],
            "error": str(e)
        })

@router.get("/follow_ups", response_class=HTMLResponse, summary="售后回访页面")
async def follow_ups_page(request: Request):
    """售后回访页面：按手机号查看客户档案并生成保养/保修回访消息"""
    return templates.TemplateResponse("follow_ups.html", {"request": request})

@router.get("/tickets", response_class=HTMLResponse, summary="报修工单管理页面")
async def tickets_page(request: Request):
    """报修工单管理页面：工单列表、派单与状态流转"""
    return templates.TemplateResponse("tickets.html", {"request": request})
