"""
操作审计日志数据访问对象

写操作/主管工具选择的审计落库；idem_key 唯一约束支撑后台写接口幂等重放
（同键重复写入返回 None，由服务层判定为 replay）。
"""

from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from ..base.interfaces import BaseAuditLogRepository
from ..base.session_manager import SessionManager
from ..models import AuditLog


class AuditLogRepository(BaseAuditLogRepository):

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    def add_log(self, actor: str, scene: str, action: str,
                resource_type: Optional[str] = None, resource_id: Optional[str] = None,
                tool_id: Optional[str] = None, risk_tier: str = 'read',
                result: str = 'ok', detail: Optional[str] = None,
                idem_key: Optional[str] = None, ip: Optional[str] = None) -> Optional[int]:
        with self.session_manager.session_scope() as session:
            row = AuditLog(
                actor=actor,
                scene=scene,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                tool_id=tool_id,
                risk_tier=risk_tier,
                result=result,
                detail=detail,
                idem_key=idem_key,
                ip=ip,
            )
            session.add(row)
            try:
                session.flush()
                return row.id
            except IntegrityError:
                # 幂等键重复 = 该写请求已被处理过（重放），调用方查 find_by_idem_key 拿缓存
                session.rollback()
                return None

    def list_logs(self, limit: int = 100, scene: Optional[str] = None,
                  action: Optional[str] = None, risk_tier: Optional[str] = None,
                  result: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            query = session.query(AuditLog)
            if scene:
                query = query.filter(AuditLog.scene == scene)
            if action:
                query = query.filter(AuditLog.action == action)
            if risk_tier:
                query = query.filter(AuditLog.risk_tier == risk_tier)
            if result:
                query = query.filter(AuditLog.result == result)
            query = query.order_by(AuditLog.id.desc()).limit(limit)
            return [self._to_dict(row) for row in query.all()]

    def find_by_idem_key(self, idem_key: str) -> Optional[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            row = session.query(AuditLog).filter(AuditLog.idem_key == idem_key).first()
            return self._to_dict(row) if row else None

    def _to_dict(self, row: AuditLog) -> Dict[str, Any]:
        return {
            'id': row.id,
            'actor': row.actor,
            'scene': row.scene,
            'action': row.action,
            'resource_type': row.resource_type,
            'resource_id': row.resource_id,
            'tool_id': row.tool_id,
            'risk_tier': row.risk_tier,
            'result': row.result,
            'detail': row.detail,
            'idem_key': row.idem_key,
            'ip': row.ip,
            'created_at': row.created_at,
        }
