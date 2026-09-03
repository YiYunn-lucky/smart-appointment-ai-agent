from typing import List, Dict, Any, Optional
from ..base.interfaces import BaseHumanHandoverRepository
from ..base.session_manager import SessionManager
from ..models import HumanHandover


class HumanHandoverRepository(BaseHumanHandoverRepository):
    """
    转人工记录数据访问对象

    投诉可能发生在尚未创建工单时，因此 ticket_id 可为空
    """

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    def create_handover(self, issue_summary: str, user_name: Optional[str] = None,
                        user_phone: Optional[str] = None, ticket_id: Optional[int] = None) -> int:
        with self.session_manager.session_scope() as session:
            handover = HumanHandover(
                user_name=user_name,
                user_phone=user_phone,
                ticket_id=ticket_id,
                issue_summary=issue_summary
            )
            session.add(handover)
            session.flush()
            return handover.id

    def get_handovers(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            handovers = session.query(HumanHandover).order_by(
                HumanHandover.created_at.desc()
            ).limit(limit).all()
            return [
                {
                    'id': h.id,
                    'user_name': h.user_name,
                    'user_phone': h.user_phone,
                    'ticket_id': h.ticket_id,
                    'ticket_no': h.ticket.ticket_no if h.ticket else None,
                    'issue_summary': h.issue_summary,
                    'created_at': h.created_at
                }
                for h in handovers
            ]
