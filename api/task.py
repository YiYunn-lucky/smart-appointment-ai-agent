"""
简化的任务分类API

只保留核心分类功能（复用聊天链路的多Agent编排）
"""
import uuid

from fastapi import APIRouter, HTTPException
from .core.response_models import (
    TaskClassificationRequest,
    DataResponse
)

router = APIRouter(prefix="/api/task", tags=["任务分类"])


@router.post("/classify", response_model=DataResponse)
async def classify_task(request: TaskClassificationRequest):
    """分类并处理任务（一次性匿名会话，与聊天主链路隔离）"""
    try:
        from api.chat_handler import ProcessUserInput_stream

        session_id = f"task-api-{uuid.uuid4().hex}"
        result = ""
        async for token in ProcessUserInput_stream(request.text, session_id=session_id):
            result += token

        return DataResponse(
            message="任务分类成功",
            data=result
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
