"""
后台操作风险分级策略（纯函数，无 IO、不依赖任何分层模块）

三档风险：
- read    只读直通（查库/检索/兜底话术，无副作用）
- confirm 关键参数操作（可产生业务影响，需操作员确认 + 审计）
- write   写库高危操作（建单/派单/登记/知识库变更，一律审计 + 幂等治理）

用途：
1. 主管工具白名单在此集中登记（与 agents/supervisor/tool_registry.py 的默认工具
   同源，一致性由测试锁校验，避免字面量漂移）；
2. 后台 HTTP 写接口按方法分级，写入必须落审计并以 Idempotency-Key 幂等；
3. 审计记录中的 risk_tier 字段即取自本策略。

注意分层约束：services 层不得 import agents，因此这里是工具风险表的唯一
字面量源头，agent 侧注册表仅重复 risk 标注供装配校验。
"""

from typing import Dict, Optional

RISK_READ = 'read'
RISK_CONFIRM = 'confirm'
RISK_WRITE = 'write'

# 风险全序：read < confirm < write
RISK_TIERS = (RISK_READ, RISK_CONFIRM, RISK_WRITE)

# 主管工具白名单 → 风险分级（工具四件套与分类枚举一一对应）
TOOL_RISK_TABLE: Dict[str, str] = {
    'repair_booking': RISK_WRITE,    # 报修建单：写工单 + 工程师忙档
    'human_handover': RISK_WRITE,    # 投诉/转人工登记落库
    'aftersale_consult': RISK_READ,  # 订单/工单查库 + 知识检索，纯读
    'fallback_reply': RISK_READ,     # 能力清单兜底话术，不触库
}

# 需幂等键治理的写工具（= confirm + write 两档工具）
WRITE_TOOL_IDS = frozenset(
    tool_id for tool_id, risk in TOOL_RISK_TABLE.items() if risk in (RISK_CONFIRM, RISK_WRITE)
)


def tool_risk(tool_id: Optional[str]) -> str:
    """查询工具风险分级；白名单外的未知工具按 write 保守处理（不允许直通）"""
    return TOOL_RISK_TABLE.get(tool_id, RISK_WRITE)


def endpoint_risk(method: Optional[str]) -> str:
    """后台 HTTP 方法分级：GET/HEAD/OPTIONS 只读直通，其余写方法一律 write"""
    return RISK_READ if (method or 'GET').upper() in ('GET', 'HEAD', 'OPTIONS') else RISK_WRITE


def requires_confirmation(risk_tier: str) -> bool:
    """confirm/write 档需要操作员确认（页面二次确认 + 幂等键），read 档直通"""
    return risk_tier not in (RISK_READ, None)


def is_whitelisted_tool(tool_id: Optional[str]) -> bool:
    """工具是否在白名单内（非白名单工具的调用一律拒绝并记录审计）"""
    return tool_id in TOOL_RISK_TABLE
