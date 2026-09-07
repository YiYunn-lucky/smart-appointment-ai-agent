"""
操作审计日志API（M15 权限与安全治理）

只读接口：按场景/动作/风险/结果过滤查询审计日志（供后台审计页面展示），
写面本身不落审计（只读直通）。
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit_logs", tags=["操作审计"])


def _fmt(dt) -> Optional[str]:
    """datetime -> 'YYYY-MM-DD HH:MM:SS' 字符串"""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return str(dt)


@router.get("", summary="审计日志列表")
async def list_audit_logs(scene: Optional[str] = None, action: Optional[str] = None,
                          risk_tier: Optional[str] = None, result: Optional[str] = None,
                          limit: int = 200):
    """查询审计日志（时间倒序），支持按场景/动作/风险分级/结果过滤"""
    try:
        from services.audit_service import AuditService

        logs = AuditService().list_logs(
            limit=min(max(limit, 1), 500),
            scene=scene or None,
            action=action or None,
            risk_tier=risk_tier or None,
            result=result or None,
        )
        return {
            "status": "success",
            "total": len(logs),
            "logs": [
                {
                    "id": row.get("id"),
                    "actor": row.get("actor"),
                    "scene": row.get("scene"),
                    "action": row.get("action"),
                    "resource_type": row.get("resource_type"),
                    "resource_id": row.get("resource_id"),
                    "tool_id": row.get("tool_id"),
                    "risk_tier": row.get("risk_tier"),
                    "result": row.get("result"),
                    "detail": row.get("detail"),
                    "ip": row.get("ip"),
                    "created_at": _fmt(row.get("created_at")),
                }
                for row in logs
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取审计日志失败: {str(e)}")
