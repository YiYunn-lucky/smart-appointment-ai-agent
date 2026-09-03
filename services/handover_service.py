"""
转人工服务层

职责：
1. 记录投诉/转人工诉求（可能发生在尚无工单时，ticket_id 可空）
2. 提供转人工记录列表供后台查看
"""

import logging
from typing import Dict, Any, List, Optional
from db.db_router import DatabaseRouter

logger = logging.getLogger(__name__)


class HandoverService:
    """转人工记录服务类"""

    def __init__(self, db_path: str = 'sqlite:///data/smart_appointment.db'):
        self.db_router = DatabaseRouter(db_path)
        self.handover_repo = self.db_router.handovers

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
            return handovers[0] if handovers else {'id': handover_id}
        except Exception as e:
            logger.error(f"创建转人工记录失败：{e}")
            return None

    def list_handovers(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取转人工记录列表"""
        return self.handover_repo.get_handovers(limit=limit)
