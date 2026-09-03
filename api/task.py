"""
简化的任务分类API

只保留核心分类功能（复用聊天链路的多Agent编排）
"""
from fastapi import APIRouter, HTTPException
from .core.response_models import (
    TaskClassificationRequest,
    DataResponse
)

router = APIRouter(prefix="/api/task", tags=["任务分类"])


@router.post("/classify", response_model=DataResponse)
async def classify_task(request: TaskClassificationRequest):
    """分类并处理任务"""
    try:
        from api.chat_handler import task_agent
        result = await task_agent.classify_task(request.text)

        return DataResponse(
            message="任务分类成功",
            data=result
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
