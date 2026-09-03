from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy import func
from config.time_config import TimeConfig
from ..base.interfaces import BaseRepairTicketRepository
from ..base.session_manager import SessionManager
from ..models import RepairTicket


class TicketRepository(BaseRepairTicketRepository):
    """
    报修工单数据访问对象

    职责：
    1. 报修工单创建、查询、状态更新
    2. 工单号（AX + 日期 + 序号）生成
    3. 派单绑定工程师
    """

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    def create_ticket(self, user_name: Optional[str], user_phone: str, product_type: str,
                      fault_desc: str, address: str, start_time: datetime, end_time: datetime,
                      engineer_id: Optional[int] = None, status: str = 'pending') -> int:
        with self.session_manager.session_scope() as session:
            ticket = RepairTicket(
                ticket_no=self.generate_ticket_no(),
                user_name=user_name,
                user_phone=user_phone,
                product_type=product_type,
                fault_desc=fault_desc,
                address=address,
                start_time=start_time,
                end_time=end_time,
                status=status,
                engineer_id=engineer_id
            )
            session.add(ticket)
            session.flush()
            return ticket.id

    def get_ticket_by_id(self, ticket_id: int) -> Optional[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            ticket = session.query(RepairTicket).filter(
                RepairTicket.id == ticket_id
            ).first()
            if not ticket:
                return None
            return self._ticket_to_dict(ticket)

    def get_ticket_by_no(self, ticket_no: str) -> Optional[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            ticket = session.query(RepairTicket).filter(
                RepairTicket.ticket_no == ticket_no
            ).first()
            if not ticket:
                return None
            return self._ticket_to_dict(ticket)

    def get_tickets(self, status: Optional[str] = None, phone: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            query = session.query(RepairTicket)
            if status:
                query = query.filter(RepairTicket.status == status)
            if phone:
                query = query.filter(RepairTicket.user_phone == phone)
            tickets = query.order_by(RepairTicket.id.desc()).all()
            return [self._ticket_to_dict(t) for t in tickets]

    def update_ticket(self, ticket_id: int, **updates) -> bool:
        with self.session_manager.session_scope() as session:
            ticket = session.query(RepairTicket).filter(
                RepairTicket.id == ticket_id
            ).first()
            if not ticket:
                return False
            for key, value in updates.items():
                if hasattr(ticket, key):
                    setattr(ticket, key, value)
            return True

    def generate_ticket_no(self) -> str:
        with self.session_manager.session_scope() as session:
            date_str = TimeConfig.now().strftime('%Y%m%d')
            prefix = f"AX{date_str}"
            today_count = session.query(func.count(RepairTicket.id)).filter(
                RepairTicket.ticket_no.like(f"{prefix}%")
            ).scalar() or 0
            return f"{prefix}{today_count + 1:02d}"

    def _ticket_to_dict(self, ticket: RepairTicket) -> Dict[str, Any]:
        return {
            'id': ticket.id,
            'ticket_no': ticket.ticket_no,
            'user_name': ticket.user_name,
            'user_phone': ticket.user_phone,
            'product_type': ticket.product_type,
            'fault_desc': ticket.fault_desc,
            'address': ticket.address,
            'start_time': ticket.start_time,
            'end_time': ticket.end_time,
            'status': ticket.status,
            'engineer_id': ticket.engineer_id,
            'engineer_name': ticket.engineer.name if ticket.engineer else None,
            'created_at': ticket.created_at,
            'updated_at': ticket.updated_at,
            'closed_at': ticket.closed_at
        }
