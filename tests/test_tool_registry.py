"""
M14 主管工具化：工具注册表与主管规划（全离线，不触网不触 LLM）

覆盖：四工具元数据（与分类枚举一一对应、风险分级、模型分级）、按类别选择与
未知类别兜底、工具清单注入分类提示词、分类处理器"观察→选工具→执行"分发、
规划复盘日志、默认分类器提示词不因清单缺失而回归。
"""

import pytest

from agents.supervisor.tool_registry import (
    SupervisorToolRegistry,
    ToolSpec,
)
from agents.task_classification.classification_processor import ClassificationProcessor
from agents.task_classification.state_manager import StateManager
from agents.task_classification.task_classifier import TaskClassifier
from conftest import FakeChatModel
from config.constants import SharedState


class TestRegistryMetadata:
    def test_four_tools_cover_all_categories(self):
        registry = SupervisorToolRegistry()
        tools = registry.list_tools()
        assert len(tools) == 4
        assert {t.category for t in tools} == {'appointment', 'query', 'complaint', 'other'}
        assert {t.tool_id for t in tools} == {
            'repair_booking', 'aftersale_consult', 'human_handover', 'fallback_reply'}

    def test_risk_tier_and_model_tier_annotated(self):
        registry = SupervisorToolRegistry()
        tools = registry.list_tools()
        booking = registry.get('repair_booking')
        consult = registry.get('aftersale_consult')
        assert booking.risk_tier == 'write'      # 写库类：报修建单
        assert consult.risk_tier == 'read'       # 只读直通：查库/知识检索
        assert registry.get('human_handover').risk_tier == 'write'
        assert registry.get('fallback_reply').risk_tier == 'read'
        # 结构化/判定入口全部标注 fast 通道（供调度层核对分级装配）
        assert all(t.model_tier == 'fast' for t in tools)

    def test_register_override_and_unknown_get(self):
        registry = SupervisorToolRegistry()
        assert registry.get('no_such_tool') is None
        custom = ToolSpec(tool_id='custom_x', name='自定义', description='x',
                          category='query', handler='consultation')
        registry.register(custom)
        assert registry.get('custom_x') is custom

    def test_manifest_text_includes_tool_semantics(self):
        text = SupervisorToolRegistry().manifest_text()
        assert '工具与类别一一对应' in text
        assert '报修登记工具' in text and 'repair_booking' in text
        assert '售后咨询工具' in text
        assert '转人工登记工具' in text
        assert '能力清单兜底工具' in text


class TestSelectTool:
    def test_category_selects_matching_tool(self):
        registry = SupervisorToolRegistry()
        assert registry.by_category('appointment').tool_id == 'repair_booking'
        assert registry.by_category('query').tool_id == 'aftersale_consult'
        assert registry.by_category('complaint').tool_id == 'human_handover'
        assert registry.by_category('other').tool_id == 'fallback_reply'

    def test_unknown_category_falls_back(self):
        registry = SupervisorToolRegistry()
        for bogus in ('pay', 'statistics', '天气', '', None):
            assert registry.by_category(bogus).tool_id == 'fallback_reply'

    def test_choose_records_plan_point(self):
        registry = SupervisorToolRegistry()
        assert registry.choose('complaint') == 'human_handover'


class _FakeRouter:
    """替身路由器：只记录被主管选中的处理方，不触碰真实服务"""

    def __init__(self):
        self.calls = []
        self.appointment_agent = object()   # 非空 = 报修专员在位
        self.consultant_agent = object()

    async def route_to_appointment(self, task):
        self.calls.append('appointment')
        yield "[REPLY]报修"

    async def route_to_consultation(self, task):
        self.calls.append('consultation')
        yield "[REPLY]咨询"

    async def route_to_complaint(self, task):
        self.calls.append('complaint')
        yield "[REPLY]转人工"

    async def handle_unsupported_task(self, category):
        self.calls.append(f'fallback:{category}')
        yield "[REPLY]兜底"


class TestSupervisorDispatch:
    def _processor(self, reply: str) -> ClassificationProcessor:
        classifier = TaskClassifier(FakeChatModel(content=reply))
        router = _FakeRouter()
        processor = ClassificationProcessor(
            classifier, StateManager(SharedState()), router, None)
        return processor, router

    @pytest.mark.asyncio
    async def test_category_drives_tool_dispatch_and_plan_log(self):
        for category, expected_call in [('appointment', 'appointment'),
                                        ('query', 'consultation'),
                                        ('complaint', 'complaint'),
                                        ('other', 'fallback:other')]:
            processor, router = self._processor(category)
            chunks = [t async for t in processor.process_task_stream("任意输入")]
            assert router.calls == [expected_call], category
            assert chunks, category
            plan = processor.plan_log[-1]
            assert plan['category'] == category

    @pytest.mark.asyncio
    async def test_unknown_category_falls_to_fallback_tool(self):
        processor, router = self._processor('pay')  # 废弃类别
        [t async for t in processor.process_task_stream("查一下账单")]
        assert router.calls == ['fallback:other']
        assert processor.plan_log[-1]['tool_id'] == 'fallback_reply'

    @pytest.mark.asyncio
    async def test_missing_subagent_falls_back_instead_of_crashing(self):
        classifier = TaskClassifier(FakeChatModel(content='appointment'))
        router = _FakeRouter()
        router.appointment_agent = None  # 报修专员不可用
        processor = ClassificationProcessor(
            classifier, StateManager(SharedState()), router, None)
        chunks = [t async for t in processor.process_task_stream("我要报修")]
        assert router.calls == ['fallback:appointment']
        assert "".join(chunks)

    def test_default_classifier_prompt_unchanged_without_manifest(self):
        # 未挂注册表的裸分类器（旧调用方）提示词不回归
        classifier = TaskClassifier(FakeChatModel())
        template = classifier.prompt.template
        assert "请将任务归类为以下类别" in template
        assert "只返回类别英文名" in template
        assert "可调用的工具清单" not in template

    def test_classifier_prompt_carries_tool_manifest(self):
        registry = SupervisorToolRegistry()
        classifier = TaskClassifier(FakeChatModel(), tools_text=registry.manifest_text())
        template = classifier.prompt.template
        assert "可调用的工具清单" in template
        assert "报修登记工具" in template
