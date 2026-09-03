"""
售后回访API（原用户行为分析API改造）

面向售后运营：按客户手机号查询维修档案、生成保养/保修回访消息，
并提供回访页运营统计（工单/订单/工程师/转人工概览）。
"""

import logging
from typing import Optional, List

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/follow_ups", tags=["售后回访"])


class AnalysisRequest(BaseModel):
    """客户手机号查询请求"""
    phone: str


class UserAnalysisResponse(BaseModel):
    """客户维修档案响应（键与 UserBehaviorAgent.get_user_analysis 对齐）"""
    phone: str = ""
    user_found: bool = False
    user_name: Optional[str] = None
    favorite_engineer_id: Optional[int] = None
    favorite_engineer_name: Optional[str] = None
    favorite_product_type: Optional[str] = None
    favorite_fault_desc: Optional[str] = None
    favorite_time_slot: Optional[str] = None
    total_repairs: int = 0
    last_repair_date: Optional[str] = None
    days_since_last_repair: Optional[int] = None
    should_send_reminder: bool = False


class ReminderRequest(BaseModel):
    """回访消息生成请求"""
    phone: str


class AvailableSlot(BaseModel):
    """工程师可约上门时段（与 user_behavior_agent 槽位结构一致）"""
    date: str = ""
    time: str = ""
    formatted: str = ""


class ReminderResponse(BaseModel):
    """回访消息响应"""
    phone: str = ""
    message: str
    engineer_available_times: Optional[List[AvailableSlot]] = None


class StatsResponse(BaseModel):
    """售后运营统计响应"""
    total_orders: int = 0
    total_tickets: int = 0
    tickets_by_status: dict = {}
    total_engineers: int = 0
    total_handovers: int = 0


async def get_user_analysis(phone: str = "13800138000") -> UserAnalysisResponse:
    """按手机号获取客户维修档案分析"""
    response = UserAnalysisResponse(phone=phone)
    try:
        from agents.user_behavior_agent import UserBehaviorAgent

        agent = UserBehaviorAgent()
        analysis = agent.get_user_analysis(phone)

        if not analysis or analysis.get('total_repairs', 0) == 0:
            return response

        # 查询客户姓名
        user_name = None
        try:
            from services.order_service import OrderService
            orders = OrderService().get_orders_by_phone(phone)
            if orders and orders[0].get('user_name'):
                user_name = orders[0]['user_name']
        except Exception as e:
            logger.warning(f"查询客户姓名失败: {e}")

        # 查询常用工程师姓名
        engineer_name = None
        if analysis.get('favorite_engineer_id'):
            try:
                from services.engineer_service import EngineerService
                tech = EngineerService().get_engineer_by_id(analysis['favorite_engineer_id'])
                if tech:
                    engineer_name = tech.get('name')
            except Exception as e:
                logger.warning(f"查询工程师姓名失败: {e}")

        response.user_found = True
        response.user_name = user_name
        response.favorite_engineer_id = analysis.get('favorite_engineer_id')
        response.favorite_engineer_name = engineer_name
        response.favorite_product_type = analysis.get('favorite_product_type')
        response.favorite_fault_desc = analysis.get('favorite_fault_desc')
        response.favorite_time_slot = analysis.get('favorite_time_slot')
        response.total_repairs = analysis.get('total_repairs', 0)
        response.last_repair_date = analysis.get('last_repair_date')
        response.days_since_last_repair = analysis.get('days_since_last_repair')
        response.should_send_reminder = analysis.get('should_send_reminder', False)
        return response
    except Exception as e:
        logger.error(f"获取客户维修档案失败: {e}")
        return response


@router.post("/analysis", response_model=UserAnalysisResponse, summary="客户维修档案分析")
async def analysis_endpoint(request: AnalysisRequest):
    """按手机号获取客户的报修档案（常用品类/故障/时段/工程师）"""
    return await get_user_analysis(request.phone)


@router.post("/reminder", response_model=ReminderResponse, summary="生成回访消息")
async def reminder_endpoint(request: ReminderRequest):
    """生成保养/保修回访消息，并附带工程师今日可约上门时段（9:00-18:00）"""
    try:
        from agents.user_behavior_agent import UserBehaviorAgent

        agent = UserBehaviorAgent()
        result = await agent.get_reminder_with_schedule(request.phone)

        if not result or not result.get("message"):
            return ReminderResponse(
                phone=request.phone,
                message="尊敬的用户您好！系统暂未查询到您的报修记录，暂无法生成回访消息。如需售后帮助，请拨打全国服务热线 400-820-9000，或直接在聊天中报修。",
                engineer_available_times=[]
            )
        return ReminderResponse(
            phone=request.phone,
            message=result["message"],
            engineer_available_times=result.get("engineer_available_times") or []
        )
    except Exception as e:
        logger.error(f"生成回访消息失败: {e}")
        return ReminderResponse(
            phone=request.phone,
            message="尊敬的客户您好！系统暂时无法生成回访消息，请稍后再试，或拨打全国服务热线 400-820-9000 咨询。",
            engineer_available_times=[]
        )


@router.get("/stats", response_model=StatsResponse, summary="售后运营统计")
async def stats_endpoint():
    """售后运营概览：订单数、工单数（按状态）、工程师数、转人工记录数"""
    stats = StatsResponse()
    try:
        from services.order_service import OrderService
        from services.ticket_service import TicketService
        from services.engineer_service import EngineerService
        from services.handover_service import HandoverService

        try:
            stats.total_orders = len(OrderService().order_repo.get_all_orders() or [])
        except Exception as e:
            logger.warning(f"统计订单失败: {e}")

        tickets = TicketService().list_tickets() or []
        stats.total_tickets = len(tickets)
        status_counts = {}
        for t in tickets:
            status = t.get('status', 'unknown')
            status_counts[status] = status_counts.get(status, 0) + 1
        stats.tickets_by_status = status_counts

        try:
            engineers = EngineerService().get_all_engineers() or []
            stats.total_engineers = len(engineers)
        except Exception as e:
            logger.warning(f"统计工程师失败: {e}")

        try:
            stats.total_handovers = len(HandoverService().list_handovers(limit=500) or [])
        except Exception as e:
            logger.warning(f"统计转人工记录失败: {e}")
    except Exception as e:
        logger.error(f"获取运营统计失败: {e}")
    return stats
