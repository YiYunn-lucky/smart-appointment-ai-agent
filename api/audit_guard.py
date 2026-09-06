"""
后台写接口审计 + 幂等公共件（M15 权限与安全治理）

每个后台写端点统一：
1. 取幂等键（Idempotency-Key 请求头优先，缺省服务端生成并回显）；
2. find_replay 短路：同键已有成功记录 → 返回幂等重放响应（不重复执行业务）；
3. 业务成功落一条 result='ok' 审计（占用幂等键）；失败落 result='error'
   （不占键、key 转存 detail，允许客户端修正后重试）。

AuditService.record 的幂等键规则见 services/audit_service.py。
"""

import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from services.audit_service import AuditService

logger = logging.getLogger(__name__)

REPLAY_FLAG = 'replay'


def request_idem_key(request: Request) -> str:
    """取幂等键：客户端显式头优先，缺省由服务端生成（保证所有写请求均可重放防护）"""
    raw = request.headers.get("idempotency-key")
    return (raw or "").strip() or str(uuid.uuid4())


def request_client_ip(request: Request) -> Optional[str]:
    """取客户端 IP（反向代理场景记录直连地址即可）"""
    return request.client.host if request.client else None


def replay_response(replay: Dict[str, Any]) -> JSONResponse:
    """幂等重放响应：该幂等键已被成功处理，返回首次记录摘要，不再重复执行业务"""
    detail = replay.get("detail") or ""
    message = f"该请求此前已处理成功（幂等重放），首次记录：{detail}"
    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "message": message,
            "replay": True,
            "log_id": replay.get("id"),
        },
        headers={"X-Replay": "true"},
    )


def new_audit_service() -> AuditService:
    """写端点审计服务（后台默认库）"""
    return AuditService()
