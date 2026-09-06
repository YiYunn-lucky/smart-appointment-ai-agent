"""
M15 权限与安全治理：风险分级策略（全离线，不触网不触 LLM）

覆盖：三档风险全序、工具白名单与主管工具注册表同源一致（防字面量漂移）、
未知工具保守处理、HTTP 方法分级、确认门槛。
"""

import pytest

from agents.supervisor.tool_registry import SupervisorToolRegistry
from services.permission_policy import (
    RISK_CONFIRM,
    RISK_READ,
    RISK_TIERS,
    RISK_WRITE,
    TOOL_RISK_TABLE,
    WRITE_TOOL_IDS,
    endpoint_risk,
    is_whitelisted_tool,
    requires_confirmation,
    tool_risk,
)


class TestRiskTiers:
    def test_three_tiers_ordered_read_confirm_write(self):
        # read < confirm < write：低档永不大于高档（治理强度递增）
        assert RISK_TIERS.index(RISK_READ) < RISK_TIERS.index(RISK_CONFIRM) < RISK_TIERS.index(RISK_WRITE)

    def test_read_needs_no_confirmation_others_do(self):
        assert requires_confirmation(RISK_READ) is False
        assert requires_confirmation(RISK_CONFIRM) is True
        assert requires_confirmation(RISK_WRITE) is True

    def test_endpoint_method_risk(self):
        assert endpoint_risk('GET') == RISK_READ
        assert endpoint_risk('get') == RISK_READ
        assert endpoint_risk('HEAD') == RISK_READ
        assert endpoint_risk('POST') == RISK_WRITE
        assert endpoint_risk('PUT') == RISK_WRITE
        assert endpoint_risk('DELETE') == RISK_WRITE
        assert endpoint_risk(None) == RISK_READ


class TestToolWhitelist:
    def test_whitelist_sync_with_supervisor_registry(self):
        """白名单与主管工具注册表必须完全一致（风险表是唯一字面量源头，防漂移）"""
        registry = SupervisorToolRegistry()
        assert set(TOOL_RISK_TABLE.keys()) == {t.tool_id for t in registry.list_tools()}
        for tool in registry.list_tools():
            assert TOOL_RISK_TABLE[tool.tool_id] == tool.risk_tier, \
                f"工具 {tool.tool_id} 风险分级不一致：policy={TOOL_RISK_TABLE[tool.tool_id]} registry={tool.risk_tier}"

    def test_write_tools_are_write_risk(self):
        assert WRITE_TOOL_IDS == {'repair_booking', 'human_handover'}

    def test_read_tools_not_in_write_list(self):
        assert 'aftersale_consult' not in WRITE_TOOL_IDS
        assert 'fallback_reply' not in WRITE_TOOL_IDS

    def test_unknown_tool_conservative_write(self):
        # 白名单外工具一律按高危处理（不允许直通），且拒绝执行
        assert tool_risk('no_such_tool') == RISK_WRITE
        assert is_whitelisted_tool('no_such_tool') is False

    def test_known_tools_whitelisted(self):
        for tool_id in TOOL_RISK_TABLE:
            assert is_whitelisted_tool(tool_id) is True

    def test_every_whitelisted_tool_has_explicit_risk(self):
        # 白名单内的工具风险必须在三档内（不允许漏标）
        for risk in TOOL_RISK_TABLE.values():
            assert risk in RISK_TIERS
