"""
操作审计服务（M15 权限与安全治理）

职责：
1. 全链路审计落库：主管工具选择 / 报修建单 / 转人工登记 / 后台工单与知识库
   写操作 / AutoDream 沉淀 —— 写面逐条可查（谁、何时、对哪个资源、成败、原因）；
2. 后台写接口幂等治理：result=='ok' 的记录占用 idem_key（唯一索引兜底），同键
   重放由 find_replay 短路并返回缓存记录；失败记录不占幂等键（key 转存 detail
   文本），客户端可安全重试直至成功；
3. 审计写入失败只告警、绝不阻断业务主流程。

幂等键约定（README/SPEC 同步说明）：
- 成功记录带键 → 重放返回缓存（HTTP 响应体标记 replay）；
- 失败记录不带键 → 重试不误伤。
"""

import logging
from typing import Any, Dict, List, Optional

from db.db_router import DatabaseRouter

logger = logging.getLogger(__name__)


class AuditService:
    """操作审计服务"""

    def __init__(self, db_path: str = 'sqlite:///data/smart_appointment.db'):
        self.db_router = DatabaseRouter(db_path)
        self.audit_log_repo = self.db_router.audit_logs

    def record(self, actor: str = 'system', scene: str = '', action: str = '',
               resource_type: Optional[str] = None, resource_id: Optional[str] = None,
               tool_id: Optional[str] = None, risk_tier: str = 'read',
               result: str = 'ok', detail: Optional[str] = None,
               idem_key: Optional[str] = None, ip: Optional[str] = None) -> Optional[int]:
        """
        写一条审计日志，返回记录 id。

        幂等语义：result != 'ok' 的记录不占用 idem_key（key 转存 detail 便于追溯），
        保证失败后重试仍能落成功记录；成功记录同键重复写入返回 None（唯一索引冲突），
        上层按重放处理。
        """
        if result != 'ok' and idem_key:
            detail = f"{detail or ''}[幂等键 {idem_key}]".strip()
            idem_key = None
        try:
            return self.audit_log_repo.add_log(
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
        except Exception as e:
            # 审计失败不阻断业务，仅告警
            logger.warning(f"审计落库失败（不影响业务）：scene={scene} action={action} err={e}")
            return None

    def find_replay(self, idem_key: Optional[str]) -> Optional[Dict[str, Any]]:
        """幂等门：同键已有成功记录 → 该请求已处理过，返回缓存记录供重放短路"""
        if not idem_key:
            return None
        row = self.audit_log_repo.find_by_idem_key(idem_key)
        return row if row and row.get('result') == 'ok' else None

    def list_logs(self, limit: int = 200, scene: Optional[str] = None,
                  action: Optional[str] = None, risk_tier: Optional[str] = None,
                  result: Optional[str] = None) -> List[Dict[str, Any]]:
        """审计日志查询（后台审计页），按时间倒序"""
        return self.audit_log_repo.list_logs(
            limit=limit, scene=scene, action=action, risk_tier=risk_tier, result=result)
