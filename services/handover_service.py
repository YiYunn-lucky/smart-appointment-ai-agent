"""
转人工服务层

职责：
1. 记录投诉/转人工诉求（可能发生在尚无工单时，ticket_id 可空）
2. 提供转人工记录列表供后台查看

审计（M15）：构造可注入 audit_logger（可选回调），登记成功落审计（写操作，
risk=write），供 Agent 链路（投诉登记）自动留痕。
"""

import logging
from typing import Callable, Dict, Any, List, Optional
from db.db_router import DatabaseRouter

logger = logging.getLogger(__name__)


class HandoverService:
    """转人工记录服务类"""

    def __init__(self, db_path: str = 'sqlite:///data/smart_appointment.db',
                 audit_logger: Optional[Callable[..., None]] = None,
                 audit_actor: str = 'system'):
        self.db_router = DatabaseRouter(db_path)
        self.handover_repo = self.db_router.handovers
        self.audit_logger = audit_logger
        self.audit_actor = audit_actor

    def _audit(self, action: str, resource_id: Optional[str] = None,
               result: str = 'ok', detail: Optional[str] = None) -> None:
        if self.audit_logger is None:
            return
        try:
            self.audit_logger(
                actor=self.audit_actor,
                scene='human_handover',
                action=action,
                resource_type='human_handover',
                resource_id=resource_id,
                tool_id=None,
                risk_tier='write',
                result=result,
                detail=detail,
            )
        except Exception as e:
            logger.warning(f"转人工审计失败（不影响业务）：{action} {e}")

    def create_handover(self, issue_summary: str, user_name: Optional[str] = None,
                        user_phone: Optional[str] = None, ticket_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """创建转人工记录并返回记录"""
        try:
            handover_id = self.handover_repo.create_handover(
                issue_summary=issue_summary,
                user_name=user_name,
                user_phone=user_phone,
                ticket_id=ticket_id
            )
            logger.info(f"转人工记录已创建：ID={handover_id}, 客户={user_phone or user_name}")
            handovers = self.handover_repo.get_handovers(limit=1)
            record = handovers[0] if handovers else {'id': handover_id}
            self._audit(action='create_handover', resource_id=str(record.get('id')),
                        detail=f"客户={user_phone or user_name or '未知'} 诉求摘要={str(issue_summary)[:80]}")
            return record
        except Exception as e:
            logger.error(f"创建转人工记录失败：{e}")
            self._audit(action='create_handover', result='error', detail=str(e)[:200])
            return None

    def list_handovers(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取转人工记录列表"""
        return self.handover_repo.get_handovers(limit=limit)
