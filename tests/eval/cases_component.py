"""
EDD 组件评测用例（M16）：真实对象协作的确定性断言（不触网）

组件 = 单个服务的多条操作 / 两个以上模块的真实协作（仓储 + 服务 + 处理器），
每例使用独立临时库文件隔离，避免与其它评测相互污染。
涉及 async 的用例以 coroutine 形式提供（run_eval 负责 asyncio.run）。
"""

import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from agents.session.agent_session_registry import AgentSessionRegistry
from agents.session.session_context import SessionContext
from agents.session.session_window import SUMMARY_MARK, fallback_summary
from agents.task_classification.classification_processor import ClassificationProcessor
from agents.task_classification.state_manager import StateManager
from agents.task_classification.task_classifier import TaskClassifier
from config.constants import SharedState
from services.dream_policy import activity_stats, is_eligible
from services.engineer_service import EngineerService
from services.memory_service import MemoryService
from services.ticket_service import TicketService
from tests.eval.base import LLMCounter, SeqFake


# ---------- 主管规划（工具选择 + 规划复盘 + 审计 + 风险分级） ----------

def component_supervisor_plan_and_audit(scratch_dir=None):
    collector = []
    fake = SeqFake(LLMCounter(), ["other"])  # 本用例只调用 select_tool，不触发分类
    processor = ClassificationProcessor(
        TaskClassifier(fake),
        StateManager(SharedState()),
        SimpleNamespace(appointment_agent=None, consultant_agent=None),
        None,
        audit_logger=lambda **kw: collector.append(kw),
    )
    expected = {
        "appointment": ("repair_booking", "write"),
        "query": ("aftersale_consult", "read"),
        "complaint": ("human_handover", "write"),
        "other": ("fallback_reply", "read"),
    }
    for category, (tool_id, risk) in expected.items():
        tool = processor.select_tool(category)
        assert tool.tool_id == tool_id
        row = collector[-1]
        assert row["actor"] == "supervisor" and row["action"] == "tool_select"
        assert row["tool_id"] == tool_id and row["risk_tier"] == risk
    assert processor.plan_log[-1] == {"category": "other", "tool_id": "fallback_reply"}
    tool = processor.select_tool("pay")  # 废弃类别 → 兜底工具仍可选中并审计
    assert tool.tool_id == "fallback_reply"
    assert len(collector) == 5


# ---------- 会话快照：写穿 → 重建运行时还原 ----------

async def _component_session_persist_restore(scratch_dir):
    db_path = f"sqlite:///{scratch_dir / 'comp_session.db'}"
    registry1 = AgentSessionRegistry(db_path=db_path)
    runtime1 = await registry1.get("c2")
    runtime1.ctx.bind_user("13800138000")
    runtime1.ctx.appointment_slots.update({"product_type": "空调", "fault_desc": "不制冷"})
    for i in range(1, 4):
        rolled = runtime1.ctx.append_turn(f"第{i}轮提问", f"第{i}轮回复")
        assert rolled == []
    registry1.persist(runtime1)

    registry2 = AgentSessionRegistry(db_path=db_path)  # 模拟进程重启：全新注册表
    runtime2 = await registry2.get("c2")
    assert runtime2 is not runtime1  # 独立运行时实例
    assert runtime2.ctx.user_id == "13800138000"
    assert runtime2.ctx.appointment_slots["product_type"] == "空调"
    assert len(runtime2.ctx.window_messages) == 6
    assert runtime2.ctx.window_messages[-1]["content"] == "第3轮回复"


def component_session_persist_restore(scratch_dir):
    return asyncio.run(_component_session_persist_restore(scratch_dir))


# ---------- 窗口容量与滚动摘要降级（LLM 失败路径） ----------

def component_window_rollover_and_summary(scratch_dir=None):
    ctx = SessionContext("c3")
    rolled_rounds = 0
    for i in range(1, 13):
        rolled = ctx.append_turn(f"消息{i}", f"回复{i}")
        if rolled:
            rolled_rounds += len(rolled) // 2
            ctx.summary_text = fallback_summary(rolled, ctx.summary_text)  # LLM 失败 → 降级摘要
    assert len(ctx.window_messages) <= 10 * 2  # 窗口永不超过容量
    assert rolled_rounds == 6  # 60% 水位持续滚动：第 7/9/11 轮各滚出 2 轮
    assert SUMMARY_MARK in ctx.summary_text  # 降级摘要带截断标记
    assert ctx.window_messages[-2:] == [{"role": "user", "content": "消息12"},
                                        {"role": "assistant", "content": "回复12"}]


# ---------- 长期记忆：召回排序 / 去重 / 会话绑定 ----------

def component_memory_recall_and_binding(scratch_dir: Path):
    db_path = f"sqlite:///{scratch_dir / 'comp_memory.db'}"
    svc = MemoryService(db_path=db_path)
    svc.add_memory("13900000000", "偏好：常用工程师张建国，上午上门", importance=0.9)
    svc.add_memory("13900000000", "咨询：如何保养洗衣机", importance=0.5)
    svc.add_memory("13900000000", "报修：空调不制冷，已约张建国上门", memory_type="repair")
    svc.add_memory("13900000000", "报修：空调不制冷，已约张建国上门", memory_type="repair")  # 同内容去重
    svc.add_memory("13900000000", "报修：冰箱结冰异响", memory_type="repair")

    recalled = svc.recall("13900000000", "空调", top_k=5)
    assert len(recalled) == 4  # 重复内容只保留一条
    assert recalled[0]["content"].startswith("偏好：常用工程师张建国")  # 重要度 0.9 居首
    contents = {r["content"] for r in recalled}
    assert len(contents) == 4

    # 会话绑定：真实链路中会话行由每轮写穿先行创建（save 为空会话快照），
    # 之后消息出现手机号即把会话归属客户（update 命中既有行）
    from services.chat_session_service import ChatSessionService
    ChatSessionService(db_path).save(session_id="mem-s1")
    svc.maybe_bind_session("mem-s1", "我要报修洗衣机，手机号 13900000000")
    assert MemoryService.extract_phone("你好") is None  # 无手机号不触发绑定（静态口径）
    row = svc.session_repo.get_session("mem-s1")
    assert row is not None and row.get("user_id") == "13900000000"


# ---------- 工单派单组件：档期冲突 / 换人 / 完成释放忙档 ----------

def component_ticket_dispatch_conflict(scratch_dir: Path):
    # 工程师种子与工单服务共用评测沙箱默认库（run_eval 已 chdir 隔离）
    EngineerService().initialize_default_engineers()
    svc = TicketService()
    from config.time_config import TimeConfig
    start = (TimeConfig.naive_now() + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)

    def _create():
        return svc.create_ticket(user_phone="13800138000", product_type="空调",
                                 fault_desc="不制冷", address="海淀区某小区",
                                 start_time=start)

    t1 = _create()
    assigned1 = svc.assign_ticket(t1["id"], 1)
    assert assigned1 and assigned1["status"] == "assigned"  # 张建国 id=1 有空档

    t2 = _create()
    assert svc.assign_ticket(t2["id"], 1) is None  # 同一时段冲突 → 派单失败（工单保留）
    assert svc.get_ticket(t2["id"])["status"] == "pending"
    changed = svc.change_engineer(t2["id"], 2)  # 换人 = 重新派给李卫东
    assert changed and changed["status"] == "assigned" and changed["engineer_id"] == 2

    assert svc.update_status(t2["id"], "junk") is None  # 非法状态拒绝
    assert svc.update_status(t1["id"], "in_progress") is not None
    assert svc.update_status(t1["id"], "completed") is not None  # 完成 → 释放忙档

    t3 = _create()
    reassigned = svc.assign_ticket(t3["id"], 1)  # t1 忙档已释放 → 可再次承接
    assert reassigned and reassigned["status"] == "assigned"


# ---------- AutoDream 资格边界（纯策略：会话数 + 24h 跨度） ----------

def component_dream_eligibility(scratch_dir=None):
    from datetime import timedelta
    from config.time_config import TimeConfig

    now = TimeConfig.naive_now()
    behaviors = [
        {"id": i, "user_id": "p1", "session_id": f"s{i}", "action_type": "repair",
         "created_at": now - timedelta(hours=offset)}
        for i, offset in enumerate([30, 26, 22, 18, 2])
    ]
    stats = activity_stats([], behaviors)
    assert stats["session_count"] == 5
    assert stats["span_hours"] >= 28
    assert is_eligible(stats) is True

    too_short = activity_stats([], [b for b in behaviors if b["id"] < 4])
    assert too_short["session_count"] == 4
    assert is_eligible(too_short) is False  # 会话数不足

    narrow = activity_stats([], [b for b in behaviors[:5] if b["id"] % 2 == 0])
    assert is_eligible(narrow) is False  # 跨度不足 24h


COMPONENT_CASES = [
    ("c01 主管规划：工具选择 + 复盘 + 审计风险档位", component_supervisor_plan_and_audit),
    ("c02 会话写穿与重启还原（独立注册表重建）", component_session_persist_restore),
    ("c03 窗口容量与滚动摘要降级（LLM 失败路径）", component_window_rollover_and_summary),
    ("c04 长期记忆：召回排序 / 去重 / 会话绑定", component_memory_recall_and_binding),
    ("c05 工单派单：档期冲突 / 换人 / 完成释放忙档", component_ticket_dispatch_conflict),
    ("c06 AutoDream 资格边界（会话数与 24h 跨度）", component_dream_eligibility),
]
