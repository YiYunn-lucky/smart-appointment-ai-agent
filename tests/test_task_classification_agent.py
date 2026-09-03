"""
任务分类（TaskClassifier）离线功能测试

覆盖（不依赖真实 LLM key，用 conftest.FakeChatModel）：
1. 分类枚举：appointment/query/complaint/other（废弃 pay/statistics）
2. 分类提示词家电化语料
3. classify_task 结果归一化与非法值兜底
4. get_category_description 类别描述
"""

import pytest
from conftest import FakeChatModel
from agents.task_classification.task_classifier import TaskClassifier


class TestTaskClassifierCategories:
    """分类枚举与语料"""

    def test_valid_categories_enum_is_appliance_scope(self):
        assert TaskClassifier.VALID_CATEGORIES == {'appointment', 'query', 'complaint', 'other'}
        # 废弃类别不应残留
        assert 'pay' not in TaskClassifier.VALID_CATEGORIES
        assert 'statistics' not in TaskClassifier.VALID_CATEGORIES

    def test_prompt_is_appliance_aftersales(self):
        classifier = TaskClassifier(FakeChatModel())
        template = classifier.prompt.template
        # 分类角色为安居家电售后
        assert "安居家电售后" in template
        # 四个类别的家电例句
        assert "空调" in template and "报修" in template
        assert "保修" in template and "AX" in template
        assert "转人工" in template
        assert "天气" in template
    def test_get_category_description(self):
        classifier = TaskClassifier(FakeChatModel())
        assert "报修" in classifier.get_category_description("appointment")
        assert "保修" in classifier.get_category_description("query")
        assert "转人工" in classifier.get_category_description("complaint")
        assert classifier.get_category_description("unknown") == "未知任务类型"


class TestTaskClassifierClassify:
    """classify_task 结果归一化与兜底"""

    def _classifier(self, content: str) -> TaskClassifier:
        return TaskClassifier(FakeChatModel(content=content))

    @pytest.mark.asyncio
    async def test_returns_valid_category_as_is(self):
        for category in ['appointment', 'query', 'complaint', 'other']:
            result = await self._classifier(category).classify_task("任意输入")
            assert result == category, category

    @pytest.mark.asyncio
    async def test_normalizes_whitespace_and_case(self):
        assert await self._classifier("  APPOINTMENT  ").classify_task("x") == "appointment"
        assert await self._classifier("Complaint\n").classify_task("x") == "complaint"

    @pytest.mark.asyncio
    async def test_invalid_category_falls_back_to_other(self):
        """LLM 返回非白名单值（含废弃类别/乱码）→ 一律兜底 other"""
        for invalid in ["pay", "statistics", "天气", "???", "appointment appointment"]:
            result = await self._classifier(invalid).classify_task("x")
            assert result == "other", f"非法分类值 {invalid} 应兜底为 other"
