"""
售后顾问（ConsultantAgent）离线功能测试

覆盖（全部不依赖 LLM / embedding / 网络）：
1. 顾问系统提示词与话术家电化（保修/收费/故障排查域）
2. 订单保修 / 工单进度 纯函数查询意图识别与答复
3. 非查询输入正确走 RAG（返回 None 由上层决定检索）
"""

from agents.consultant.prompt_builder import PromptBuilder
from agents.consultant.consultation_processor import ConsultationProcessor


class TestConsultantPromptCopywriting:
    """售后顾问提示词语料家电化"""

    def test_system_prompt_is_appliance_aftersales(self):
        prompt = PromptBuilder().system_prompt
        assert "安居家电" in prompt
        assert "售后顾问" in prompt
        # 领域覆盖：保修 / 收费 / 故障排查 / 400 热线
        assert "保修" in prompt or "在保" in prompt
        assert "400-820-9000" in prompt or "订单号" in prompt
    def test_consultation_prompt_assembles_knowledge_and_question(self):
        builder = PromptBuilder()
        prompt = builder.build_consultation_prompt(
            "冰箱制冷效果差怎么处理？",
            [{"content": "冰箱保养知识", "score": 0.9}],
        )
        assert "安居家电" in prompt
        assert "冰箱制冷效果差怎么处理？" in prompt
        assert "冰箱保养知识" in prompt

    def test_knowledge_gap_fallback_mentions_hotline(self):
        builder = PromptBuilder()
        prompt = builder.build_consultation_prompt("空调异响", [])
        assert "400-820-9000" in prompt or "订单号" in prompt

    def test_unrelated_message_token(self):
        from agents.consultant.response_generator import ResponseGenerator

        generator = ResponseGenerator(llm=None)
        msg = generator.create_unrelated_message()
        assert "售后顾问" in msg


class TestLookupPureFunctions:
    """订单保修 / 工单进度查询纯函数（真实演示库 / 空库两种环境均稳定）"""

    def _processor(self):
        # 三个组件均可为 None：_try_lookup_answer 只依赖自身正则与查询服务
        return ConsultationProcessor(None, None, None)

    def test_ticket_no_extraction_accepts_ax_prefix(self):
        processor = self._processor()
        assert processor._extract_ticket_no("帮我看看报修单 AX2099123101 的进度") == "AX2099123101"
        assert processor._extract_ticket_no("AX2099123101 现在怎么样了") == "AX2099123101"

    def test_digits_only_ticket_no_requires_intent_keyword(self):
        processor = self._processor()
        # 含"进度/单号"意图且为 11 位以上纯数字（非手机号形态）→ 补 AX 前缀
        no = processor._extract_ticket_no("查一下 209912310011 单号的进度")
        assert no is not None and no.startswith("AX")
        # 无意图关键词时不把纯数字当单号
        assert processor._extract_ticket_no("我家电话是 209912310011") is None
        # 位数不足（10 位）不足以判为单号
        assert processor._extract_ticket_no("查一下 2099123101 单号的进度") is None

    def test_phone_extraction(self):
        processor = self._processor()
        assert processor._extract_phone("我的手机13800138000，帮我查") == "13800138000"

    def test_unknown_ticket_returns_guidance(self):
        """不存在的单号 → 引导核对单号/人工热线（不崩、可空库运行）"""
        processor = self._processor()
        answer = processor._try_lookup_answer("帮我查一下报修单 AX2099123199 的进度")
        assert answer is not None
        assert "AX2099123199" in answer
        assert "未查询到" in answer
        assert "400-820-9000" in answer

    def test_unknown_phone_warranty_query_returns_guidance(self):
        """不存在的手机号 + 保修意图 → 引导核对手机号"""
        processor = self._processor()
        answer = processor._try_lookup_answer("19900000000 买的冰箱还在保修期吗")
        assert answer is not None
        assert "未查询到" in answer
        assert "19900000000" in answer

    def test_pure_phone_without_intent_returns_none(self):
        """只有手机号、无保修/订单关键词 → 走 RAG，不触发查表"""
        processor = self._processor()
        assert processor._try_lookup_answer("帮我看看 13800138000") is None

    def test_general_after_sales_question_returns_none(self):
        """普通售后咨询（不涉单号/保修查询）→ None，交由 RAG"""
        processor = self._processor()
        for question in ["冰箱怎么保养？", "上门维修要收费吗", "你们几点下班"]:
            assert processor._try_lookup_answer(question) is None, question

    def test_ticket_status_answer_has_structure(self):
        """单号查询必然命中查表分支：要么找到状态详情，要么返回未查询到引导"""
        processor = self._processor()
        answer = processor._try_lookup_answer("报修单 AX2099123199 现在什么进度")
        assert answer is not None
        assert any(token in answer for token in ["未查询到", "状态", "工单"])
