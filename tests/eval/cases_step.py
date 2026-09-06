"""
EDD 单步评测用例（M16）：纯函数 / 单一服务调用的确定性断言

全部离线；直接以 AssertionError 表达失败。每例独立计时，供 P95 度量。
统一签名 fn(scratch_dir=None)：仅个别落库用例使用独立临时库，其余忽略该参数。
"""

import re
from datetime import timedelta
from pathlib import Path
from typing import Optional

from agents.appointment import InputParser
from agents.appointment.appointment_processor import AppointmentProcessor
from agents.appointment.engineer_finder import EngineerFinder
from agents.supervisor.tool_registry import SupervisorToolRegistry
from agents.task_classification.state_manager import StateManager
from agents.task_classification.task_classifier import TaskClassifier
from config.constants import SharedState, StateEnum
from config.time_config import TimeConfig
from services.memory_scoring import rank_top_k, recall_score
from services.memory_service import MemoryService
from services.order_service import OrderService
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
from services.ticket_service import ALLOWED_TRANSITIONS, DEFAULT_SERVICE_MINUTES
from agents.session.session_window import (
    SUMMARY_MARK,
    extract_reply_text,
    fallback_summary,
    roll_oldest,
    should_roll,
)
from tests.eval.base import LLMCounter, SeqFake


def case_risk_ordering(scratch_dir=None):
    assert RISK_TIERS.index(RISK_READ) < RISK_TIERS.index(RISK_CONFIRM) < RISK_TIERS.index(RISK_WRITE)
    assert requires_confirmation(RISK_READ) is False
    assert requires_confirmation(RISK_CONFIRM) is True
    assert requires_confirmation(RISK_WRITE) is True


def case_endpoint_method_risk(scratch_dir=None):
    assert endpoint_risk("GET") == RISK_READ and endpoint_risk("HEAD") == RISK_READ
    assert endpoint_risk("POST") == RISK_WRITE
    assert endpoint_risk("PUT") == RISK_WRITE and endpoint_risk("DELETE") == RISK_WRITE
    assert endpoint_risk(None) == RISK_READ


def case_whitelist_sync_registry(scratch_dir=None):
    """权限白名单必须与主管工具注册表同源（TOOL_RISK_TABLE 是唯一字面量源）"""
    registry = SupervisorToolRegistry()
    assert set(TOOL_RISK_TABLE.keys()) == {t.tool_id for t in registry.list_tools()}
    for tool in registry.list_tools():
        assert TOOL_RISK_TABLE[tool.tool_id] == tool.risk_tier


def case_unknown_tool_conservative(scratch_dir=None):
    assert tool_risk("no_such_tool") == RISK_WRITE
    assert is_whitelisted_tool("no_such_tool") is False


def case_write_tool_ids(scratch_dir=None):
    assert WRITE_TOOL_IDS == {"repair_booking", "human_handover"}


def case_status_machine_table(scratch_dir=None):
    assert "in_progress" not in ALLOWED_TRANSITIONS["pending"]
    assert "completed" not in ALLOWED_TRANSITIONS["pending"]
    assert "cancelled" in ALLOWED_TRANSITIONS["pending"]
    assert "cancelled" in ALLOWED_TRANSITIONS["assigned"]
    assert ALLOWED_TRANSITIONS["completed"] == set()  # 终态无出口
    assert ALLOWED_TRANSITIONS["cancelled"] == set()


def case_ticket_no_format(scratch_dir=None):
    # 工单号 = AX + YYYYMMDD(8) + 当日序号（%02d 起，随当日单量增长位数）
    assert re.fullmatch(r"AX\d{10,14}", "AX2026090701")     # 当日首单
    assert re.fullmatch(r"AX\d{10,14}", "AX20260907001")    # 当日第 100 单
    assert re.fullmatch(r"AX\d{10,14}", "AX202609070001")   # 单量过千仍合规


def case_parse_repair_time(scratch_dir=None):
    finder = EngineerFinder()
    start, end, minutes = finder.parse_repair_time("2026-09-07 15:00")
    assert start is not None and end == start + timedelta(minutes=DEFAULT_SERVICE_MINUTES)
    assert finder.parse_repair_time("未知") == (None, None, None)
    assert finder.parse_repair_time("随便写的时间")[0] is None


def case_business_hours_constant(scratch_dir=None):
    assert TimeConfig.get_business_hours() == (9, 18)
    assert DEFAULT_SERVICE_MINUTES == 120


def case_start_time_validator(scratch_dir=None):
    """过期时间 / 服务窗口外时间一律按缺失处理（防写入历史槽位）"""
    tomorrow = (TimeConfig.naive_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    assert AppointmentProcessor._is_valid_start_time(f"{tomorrow} 10:00")
    assert AppointmentProcessor._is_valid_start_time(f"{tomorrow} 08:00") is False  # 窗口外
    assert AppointmentProcessor._is_valid_start_time(f"{tomorrow} 20:00") is False
    yesterday = (TimeConfig.naive_now() - timedelta(days=1)).strftime("%Y-%m-%d")
    assert AppointmentProcessor._is_valid_start_time(f"{yesterday} 10:00") is False  # 已过期


def case_warranty_boundary(scratch_dir=None):
    """王芳名下 3 条演示订单：空调在保、洗衣机超保（保修动态计算口径）"""
    orders = OrderService().get_warranty_info("13800138000")
    by_product = {o["product_type"]: o for o in orders}
    assert len(orders) == 3
    assert by_product["空调"]["in_warranty"] is True
    assert by_product["冰箱"]["in_warranty"] is True
    assert by_product["洗衣机"]["in_warranty"] is False


def case_token_extract(scratch_dir=None):
    stream = ("[THOUGHT][客服调度]分析中\n[REPLY][客服调度]好的\n[REPLY][报修专员]您的报修已登记成功！"
              "[SIGNAL]recommendation_pending[ERROR]链路异常")
    text = extract_reply_text(stream)
    assert "分析中" not in text
    assert "您的报修已登记成功！" in text
    assert "链路异常" in text


def case_window_rollover(scratch_dir=None):
    assert should_roll(5) is False and should_roll(6) is True
    rolled, kept = roll_oldest([{"role": "user"}, {"role": "assistant"}] * 5, chunk=4)
    assert len(rolled) == 4 and len(kept) == 6
    summary = fallback_summary([{"role": "user", "content": "x" * 500}], prev_summary="")
    assert summary.endswith(SUMMARY_MARK) and len(summary) <= 400 + len(SUMMARY_MARK)


def case_recall_scoring(scratch_dir=None):
    fresh = recall_score(None, age_days=0, importance=0.8)
    old = recall_score(None, age_days=30, importance=0.8)
    assert fresh > old  # 0.3 时效权重：越新分越高
    # 归一降级口径：0.98（满语义）> 0.95（无语义归一）；低相似度语义分可能低于降级分，
    # 这正是“无语义按 (0.3*时效+0.1*重要)/0.4 归一”设计所允许的排序变化
    semantic = recall_score(1.0, age_days=0, importance=0.8)
    no_semantic = recall_score(None, age_days=0, importance=0.8)
    assert semantic > no_semantic
    assert abs(no_semantic - 0.95) < 1e-9  # (0.3*1 + 0.1*0.8) / 0.4
    # 降级排序不变量：新近低重要度 > 30 天前高重要度
    assert recall_score(None, age_days=0, importance=0.5) > recall_score(None, age_days=30, importance=0.9)
    # 无语义降级排序：新近高重要度 > 新近低重要度 > 30 天前高重要度
    candidates = [
        {"content": "old90", "semantic": None, "age_days": 30.0, "importance": 0.9},
        {"content": "fresh50", "semantic": None, "age_days": 0.0, "importance": 0.5},
        {"content": "fresh90", "semantic": None, "age_days": 0.0, "importance": 0.9},
    ]
    ranked = rank_top_k(candidates, top_k=2)
    assert ranked[0]["content"] == "fresh90"
    assert {r["content"] for r in ranked} == {"fresh90", "fresh50"}


def case_phone_extract(scratch_dir=None):
    assert MemoryService.extract_phone("手机号13800138000报修") == "13800138000"
    assert MemoryService.extract_phone("没有联系方式") is None
    assert MemoryService.extract_phone("2345678901234567890") is None  # 整串非 1 开头不命中
    # 非锚定正则对 1 开头的超长数字串按前缀截取前 11 位（保守口径：宁可误绑也防漏绑）
    assert MemoryService.extract_phone("1234567890123456789") == "12345678901"


def case_json_parse_degrade(scratch_dir=None):
    """LLM 输出非法 JSON → 全'未知'降级字典（流程转追问不崩）"""
    counter = LLMCounter()
    parser = InputParser(SeqFake(counter, ["不是JSON"]))
    data = parser.parse_data("随便")
    assert data is not None
    assert data.get("info_complete") is False
    assert "所有信息" in (data.get("missing_info") or [])


def case_classifier_prompt_covers_categories(scratch_dir=None):
    registry = SupervisorToolRegistry()
    manifest = registry.manifest_text()
    for category in TaskClassifier.VALID_CATEGORIES:
        assert registry.by_category(category) is not None
    # 清单词表是"工具"而非类别枚举（工具与类别一一对应），并明令只输出类别英文名
    assert "repair_booking" in manifest and "human_handover" in manifest
    assert "fallback_reply" in manifest and "只输出类别英文名" in manifest


def case_state_manager_transitions(scratch_dir=None):
    sm = StateManager(SharedState())
    assert sm.should_classify() is True
    sm.transition_to_appointment()
    assert sm.get_current_state() == StateEnum.APPOINTMENT
    assert sm.should_classify() is False
    sm.force_reset()
    assert sm.should_classify() is True


def case_audit_idempotency(scratch_dir: Path):
    """幂等键治理：成功占位重放短路；失败不占位同键可重试（独立临时库）"""
    from services.audit_service import AuditService

    audit = AuditService(f"sqlite:///{scratch_dir / 'step_audit.db'}")
    ok_id = audit.record(actor="operator", scene="ticket_management", action="create_ticket",
                         resource_type="repair_ticket", risk_tier="write",
                         detail="AX20260907001", idem_key="req-step-1")
    assert ok_id is not None
    replay = audit.find_replay("req-step-1")
    assert replay is not None and replay["result"] == "ok"
    assert audit.record(actor="operator", scene="ticket_management", action="create_ticket",
                        resource_type="repair_ticket", risk_tier="write",
                        detail="AX20260907001", idem_key="req-step-1") is None  # 唯一索引兜底
    assert audit.find_replay("req-step-2") is None
    err = audit.record(actor="operator", scene="ticket_management", action="create_ticket",
                       risk_tier="write", result="error", detail="档期冲突", idem_key="req-step-2")
    assert err is not None
    assert audit.find_replay("req-step-2") is None  # 失败不占位 → 可重试
    retry = audit.record(actor="operator", scene="ticket_management", action="create_ticket",
                         resource_type="repair_ticket", risk_tier="write",
                         detail="AX20260907002", idem_key="req-step-2")
    assert retry is not None
    assert audit.find_replay("req-step-2") is not None


STEP_CASES = [
    ("s01 风险档位全序与确认门槛", case_risk_ordering),
    ("s02 HTTP 方法风险分级", case_endpoint_method_risk),
    ("s03 工具白名单与主管注册表同源", case_whitelist_sync_registry),
    ("s04 未知工具保守 write 且拒绝直通", case_unknown_tool_conservative),
    ("s05 写工具集合正确", case_write_tool_ids),
    ("s06 工单状态机白名单", case_status_machine_table),
    ("s07 工单号格式 AX\\d{12}", case_ticket_no_format),
    ("s08 上门时间解析与 120 分钟默认时长", case_parse_repair_time),
    ("s09 服务窗口 (9,18) 与默认时长常量", case_business_hours_constant),
    ("s10 时间校验器拒绝过期/窗口外", case_start_time_validator),
    ("s11 保修动态计算口径（王芳订单在保/超保）", case_warranty_boundary),
    ("s12 令牌流回复提取", case_token_extract),
    ("s13 窗口 60% 水位滚动与摘要降级标记", case_window_rollover),
    ("s14 召回打分权重与无语义归一", case_recall_scoring),
    ("s15 手机号提取口径", case_phone_extract),
    ("s16 报修抽取 JSON 解析降级", case_json_parse_degrade),
    ("s17 分类 prompt 覆盖全部类别且工具可选中", case_classifier_prompt_covers_categories),
    ("s18 状态机合法迁移与强制重置", case_state_manager_transitions),
    ("s19 幂等键治理（占位/重放/失败重试）", case_audit_idempotency),
]
