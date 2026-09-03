"""
报修工单管理API

提供工单列表/创建/查询/派单/状态流转以及转人工记录查询接口，
供后台工单管理页面使用。
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tickets", tags=["报修工单管理"])


def _fmt(dt) -> Optional[str]:
    """datetime -> 'YYYY-MM-DD HH:MM' 字符串"""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M")
    return str(dt)


class TicketCreate(BaseModel):
    """创建工单请求"""
    user_name: Optional[str] = None
    user_phone: str
    product_type: str
    fault_desc: str
    address: str
    start_time: datetime
    end_time: Optional[datetime] = None
    engineer_id: Optional[int] = None


class TicketAssign(BaseModel):
    """派单请求"""
    engineer_id: int


class TicketStatusUpdate(BaseModel):
    """状态流转请求"""
    status: str


class TicketResponse(BaseModel):
    """工单响应"""
    id: int
    ticket_no: str
    user_name: Optional[str] = None
    user_phone: str
    product_type: str
    fault_desc: str
    address: str
    start_time: str
    end_time: str
    status: str
    engineer_id: Optional[int] = None
    engineer_name: Optional[str] = None
    closed_at: Optional[str] = None
    created_at: str


class HandoverResponse(BaseModel):
    """转人工记录响应"""
    id: int
    user_name: Optional[str] = None
    user_phone: Optional[str] = None
    ticket_id: Optional[int] = None
    ticket_no: Optional[str] = None
    issue_summary: str
    created_at: str


class OperationResult(BaseModel):
    """操作结果响应"""
    status: str = "success"
    message: str
    data: Optional[TicketResponse] = None


def _to_ticket_response(ticket: dict) -> TicketResponse:
    return TicketResponse(
        id=ticket["id"],
        ticket_no=ticket["ticket_no"],
        user_name=ticket.get("user_name"),
        user_phone=ticket.get("user_phone", ""),
        product_type=ticket.get("product_type", ""),
        fault_desc=ticket.get("fault_desc", ""),
        address=ticket.get("address", ""),
        start_time=_fmt(ticket.get("start_time")) or "",
        end_time=_fmt(ticket.get("end_time")) or "",
        status=ticket.get("status", ""),
        engineer_id=ticket.get("engineer_id"),
        engineer_name=ticket.get("engineer_name"),
        closed_at=_fmt(ticket.get("closed_at")),
        created_at=_fmt(ticket.get("created_at")) or ""
    )


@router.get("/handovers", response_model=List[HandoverResponse], summary="转人工记录列表")
async def list_handovers():
    """获取转人工/投诉记录（须先于 /{ticket_id} 声明）"""
    try:
        from services.handover_service import HandoverService
        records = HandoverService().list_handovers(limit=100) or []
        return [
            HandoverResponse(
                id=r["id"],
                user_name=r.get("user_name"),
                user_phone=r.get("user_phone"),
                ticket_id=r.get("ticket_id"),
                ticket_no=r.get("ticket_no"),
                issue_summary=r.get("issue_summary", ""),
                created_at=_fmt(r.get("created_at")) or ""
            )
            for r in records
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取转人工记录失败: {str(e)}")


@router.get("", response_model=List[TicketResponse], summary="工单列表")
async def list_tickets(status: Optional[str] = None, phone: Optional[str] = None):
    """获取工单列表，可按状态/手机号筛选"""
    try:
        from services.ticket_service import TicketService
        tickets = TicketService().list_tickets(status=status, phone=phone) or []
        return [_to_ticket_response(t) for t in tickets]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取工单列表失败: {str(e)}")


@router.post("", response_model=TicketResponse, summary="创建工单")
async def create_ticket(request: TicketCreate):
    """创建报修工单；如指定工程师则立即尝试派单"""
    try:
        from services.ticket_service import TicketService
        service = TicketService()
        engineer_id = request.engineer_id
        ticket = service.create_ticket(
            user_phone=request.user_phone,
            product_type=request.product_type,
            fault_desc=request.fault_desc,
            address=request.address,
            start_time=request.start_time,
            end_time=request.end_time,
            user_name=request.user_name,
            engineer_id=None
        )
        if not ticket:
            raise HTTPException(status_code=500, detail="工单创建失败")

        if engineer_id:
            assigned = service.assign_ticket(ticket["id"], engineer_id)
            if not assigned:
                # 工单已按 pending 保留，可稍后改派其他工程师
                raise HTTPException(
                    status_code=400,
                    detail=f"派单失败：工程师 {engineer_id} 在该上门时段档期冲突，工单已保留为待派单状态，可更换工程师后重新派单"
                )
            ticket = assigned
        return _to_ticket_response(ticket)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建工单失败: {str(e)}")


@router.get("/{ticket_id}", response_model=TicketResponse, summary="工单详情")
async def get_ticket(ticket_id: int):
    """获取单个工单详情"""
    try:
        from services.ticket_service import TicketService
        ticket = TicketService().get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="工单不存在")
        return _to_ticket_response(ticket)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取工单失败: {str(e)}")


@router.post("/{ticket_id}/assign", response_model=OperationResult, summary="派单")
async def assign_ticket(ticket_id: int, request: TicketAssign):
    """将工单派给指定工程师（校验档期并写忙档）"""
    try:
        from services.ticket_service import TicketService
        service = TicketService()
        ticket = service.get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="工单不存在")

        updated = service.assign_ticket(ticket_id, request.engineer_id)
        if not updated:
            from services.engineer_service import EngineerService
            tech = EngineerService().get_engineer_by_id(request.engineer_id)
            tech_name = tech.get('name') if tech else f"#{request.engineer_id}"
            raise HTTPException(
                status_code=400,
                detail=f"派单失败：{tech_name}工程师在该上门时段档期冲突，或工单当前状态不允许派单"
            )
        return OperationResult(message=f"派单成功：工单 {updated['ticket_no']} 已分配给 {updated['engineer_name']}",
                               data=_to_ticket_response(updated))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"派单失败: {str(e)}")


@router.post("/{ticket_id}/status", response_model=OperationResult, summary="状态流转")
async def update_ticket_status(ticket_id: int, request: TicketStatusUpdate):
    """工单状态流转（pending/assigned/in_progress/completed/cancelled，白名单校验）"""
    try:
        from services.ticket_service import TicketService
        service = TicketService()
        ticket = service.get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="工单不存在")

        updated = service.update_status(ticket_id, request.status)
        if not updated:
            raise HTTPException(
                status_code=400,
                detail=f"状态流转失败：工单当前状态为 {ticket['status']}，不能变更为 {request.status}"
            )
        return OperationResult(
            message=f"工单 {updated['ticket_no']} 状态已更新为「{updated['status']}」",
            data=_to_ticket_response(updated)
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"状态流转失败: {str(e)}")
