"""
报修工单服务层

职责：
1. 报修工单创建、查询与状态流转（白名单校验）
2. 派单：写工程师忙档 + 绑定工单 + 置 assigned
3. 工单号 AX + YYYYMMDD + 当日序号

审计（M15）：构造可注入 audit_logger（可选回调），成功与异常路径分别落
scene='ticket_management' 审计；后台 HTTP 写接口的幂等审计由 API 层负责，
此处回调主要服务 Agent 链路（报修专员建单）自动落审计。
"""

import logging
from typing import Callable, Dict, Any, List, Optional
from datetime import datetime, timedelta
from db.db_router import DatabaseRouter
from config.time_config import TimeConfig

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_MINUTES = 120  # 默认上门维修时长

# 工单状态流转白名单
ALLOWED_TRANSITIONS = {
    'pending': {'assigned', 'cancelled'},
    'assigned': {'in_progress', 'cancelled'},
    'in_progress': {'completed', 'cancelled'},
    'completed': set(),
    'cancelled': set(),
}

VALID_STATUSES = set(ALLOWED_TRANSITIONS.keys())


class TicketService:
    """报修工单服务类"""

    def __init__(self, db_path: str = 'sqlite:///data/smart_appointment.db',
                 audit_logger: Optional[Callable[..., None]] = None,
                 audit_actor: str = 'system'):
        self.db_router = DatabaseRouter(db_path)
        self.ticket_repo = self.db_router.tickets
        self.engineer_repo = self.db_router.engineers
        # M15 审计回调（keyword-only 全形参；缺省不记录，老调用方零影响）
        self.audit_logger = audit_logger
        self.audit_actor = audit_actor

    def _audit(self, action: str, resource_id: Optional[str] = None,
               result: str = 'ok', detail: Optional[str] = None) -> None:
        """写审计（成功 ok / 异常 error）；审计异常只告警不阻断业务"""
        if self.audit_logger is None:
            return
        try:
            self.audit_logger(
                actor=self.audit_actor,
                scene='ticket_management',
                action=action,
                resource_type='repair_ticket',
                resource_id=resource_id,
                tool_id=None,
                risk_tier='write',
                result=result,
                detail=detail,
            )
        except Exception as e:
            logger.warning(f"工单审计失败（不影响业务）：{action} {e}")

    def create_ticket(self, user_phone: str, product_type: str, fault_desc: str, address: str,
                      start_time: datetime, end_time: Optional[datetime] = None,
                      user_name: Optional[str] = None, engineer_id: Optional[int] = None,
                      status: str = 'pending') -> Optional[Dict[str, Any]]:
        """创建报修工单（默认 pending，未派单）"""
        try:
            if end_time is None:
                end_time = start_time + timedelta(minutes=DEFAULT_SERVICE_MINUTES)
            ticket_id = self.ticket_repo.create_ticket(
                user_name=user_name,
                user_phone=user_phone,
                product_type=product_type,
                fault_desc=fault_desc,
                address=address,
                start_time=start_time,
                end_time=end_time,
                engineer_id=engineer_id,
                status=status
            )
            ticket = self.ticket_repo.get_ticket_by_id(ticket_id)
            logger.info(f"报修工单已创建：{ticket['ticket_no']}, 状态={status}, 上门时间={start_time} 至 {end_time}")
            self._audit(action='create_ticket', resource_id=ticket['ticket_no'],
                        detail=f"品类={product_type} 故障={fault_desc} 上门={start_time.strftime('%Y-%m-%d %H:%M')}")
            return ticket
        except Exception as e:
            logger.error(f"创建报修工单失败：{e}")
            self._audit(action='create_ticket', result='error', detail=str(e)[:200])
            return None

    def get_ticket(self, ticket_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取工单"""
        return self.ticket_repo.get_ticket_by_id(ticket_id)

    def get_ticket_by_no(self, ticket_no: str) -> Optional[Dict[str, Any]]:
        """根据工单号获取工单"""
        return self.ticket_repo.get_ticket_by_no(ticket_no)

    def list_tickets(self, status: Optional[str] = None, phone: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取工单列表，支持状态/手机号过滤"""
        return self.ticket_repo.get_tickets(status=status, phone=phone)

    def assign_ticket(self, ticket_id: int, engineer_id: int) -> Optional[Dict[str, Any]]:
        """派单：校验工程师档期后写 busy 排班并绑定工单，置 assigned"""
        try:
            ticket = self.ticket_repo.get_ticket_by_id(ticket_id)
            if not ticket:
                logger.error(f"派单失败：工单 {ticket_id} 不存在")
                return None
            if ticket['status'] not in ('pending', 'assigned'):
                logger.error(f"派单失败：工单 {ticket['ticket_no']} 当前状态 {ticket['status']} 不允许派单")
                return None

            if not self.engineer_repo.is_engineer_available(engineer_id, ticket['start_time'], ticket['end_time']):
                logger.error(f"派单失败：工程师 {engineer_id} 在 {ticket['start_time']} 档期冲突")
                return None

            self.engineer_repo.add_schedule(
                engineer_id=engineer_id,
                start_time=ticket['start_time'],
                end_time=ticket['end_time'],
                status="busy",
                ticket_id=ticket_id
            )
            self.ticket_repo.update_ticket(ticket_id, engineer_id=engineer_id, status='assigned')
            updated = self.ticket_repo.get_ticket_by_id(ticket_id)
            logger.info(f"派单成功：工单 {updated['ticket_no']} -> 工程师 {updated['engineer_name']}")
            self._audit(action='assign_ticket', resource_id=updated['ticket_no'],
                        detail=f"工程师={updated.get('engineer_name')} 上门={str(updated['start_time'])[:16]}")
            return updated
        except Exception as e:
            logger.error(f"派单失败：{e}")
            self._audit(action='assign_ticket', result='error', detail=str(e)[:200])
            return None

    def change_engineer(self, ticket_id: int, engineer_id: int) -> Optional[Dict[str, Any]]:
        """已派单状态下更换工程师：释放原忙档再重新派单"""
        try:
            ticket = self.ticket_repo.get_ticket_by_id(ticket_id)
            if not ticket or ticket['status'] not in ('pending', 'assigned'):
                return None
            if ticket.get('engineer_id'):
                self.engineer_repo.release_schedule_by_ticket(ticket_id)
                self.ticket_repo.update_ticket(ticket_id, engineer_id=None, status='pending')
            assigned = self.assign_ticket(ticket_id, engineer_id)
            if assigned:
                self._audit(action='change_engineer', resource_id=assigned['ticket_no'],
                            detail=f"新工程师={assigned.get('engineer_name')}")
            return assigned
        except Exception as e:
            logger.error(f"更换工程师失败：{e}")
            self._audit(action='change_engineer', result='error', detail=str(e)[:200])
            return None

    def update_status(self, ticket_id: int, new_status: str) -> Optional[Dict[str, Any]]:
        """工单状态流转（白名单校验）；取消时释放工程师忙档"""
        try:
            if new_status not in VALID_STATUSES:
                logger.error(f"状态流转失败：非法状态 {new_status}")
                return None
            ticket = self.ticket_repo.get_ticket_by_id(ticket_id)
            if not ticket:
                logger.error(f"状态流转失败：工单 {ticket_id} 不存在")
                return None

            current = ticket['status']
            if new_status not in ALLOWED_TRANSITIONS.get(current, set()):
                logger.error(f"状态流转失败：{current} -> {new_status} 不在白名单内")
                return None

            updates = {'status': new_status}
            if new_status in ('completed', 'cancelled'):
                updates['closed_at'] = TimeConfig.naive_now()
                # 工单结束（完成/取消）即释放工程师忙档，工程师可承接新工单
                self.engineer_repo.release_schedule_by_ticket(ticket_id)

            self.ticket_repo.update_ticket(ticket_id, **updates)
            updated = self.ticket_repo.get_ticket_by_id(ticket_id)
            logger.info(f"工单 {updated['ticket_no']} 状态流转：{current} -> {new_status}")
            self._audit(action='update_status', resource_id=updated['ticket_no'],
                        detail=f"{current} -> {new_status}")
            return updated
        except Exception as e:
            logger.error(f"工单状态流转失败：{e}")
            self._audit(action='update_status', result='error', detail=str(e)[:200])
            return None
