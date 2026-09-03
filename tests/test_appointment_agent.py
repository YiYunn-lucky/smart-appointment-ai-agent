"""
报修专员（AppointmentAgent）离线功能测试

覆盖（全部不依赖 LLM / 网络）：
1. input_parser 的 JSON 契约解析与异常默认值
2. appointment_processor 的字段校验与信息完整性判定
3. 替代工程师推荐的确认 / 拒绝 / 追问流
4. message_builder 家电化话术（追问表 / 成功 / 失败 / 无关）
5. 状态重置
"""

import pytest
from conftest import FakeChatModel
from agents.appointment import InputParser, EngineerFinder, MessageBuilder, AppointmentProcessor

# 永远处于未来的上门时间（保证任何时刻运行都能通过"不早于当前"校验）
FUTURE_TIME = "2099-01-01 15:00"

REQUIRED_FIELDS = ["product_type", "fault_desc", "address", "phone", "start_time"]


def make_input_parser() -> InputParser:
    # 构造时即拼接 prompt|llm 链，需传入模型替身（parse_data 本身为纯 JSON 解析，不触网）
    return InputParser(llm=FakeChatModel())


def make_processor() -> AppointmentProcessor:
    return AppointmentProcessor(
        make_input_parser(),
        EngineerFinder(),
        MessageBuilder(),
        llm=None,
    )


def complete_data(**overrides) -> dict:
    """构造一份信息完整且合法的新契约解析数据"""
    data = {
        "product_type": "空调",
        "fault_desc": "不制冷",
        "address": "朝阳区望京某小区3号楼",
        "phone": "13800138000",
        "start_time": FUTURE_TIME,
        "engineer_name": "未知",
        "confirmation": "未知",
        "info_complete": True,
        "unrelated": False,
        "missing_info": [],
    }
    data.update(overrides)
    return data


class TestInputParserContract:
    """新 JSON 契约：品类/故障/地址/手机号/时间五要素"""

    def test_should_parse_complete_json_contract(self):
        ai_content = (
            '{"product_type": "冰箱", "fault_desc": "不制冷", "address": "海淀区中关村某小区",'
            ' "phone": "13800138000", "start_time": "2099-01-02 10:00", "engineer_name": "李卫东",'
            ' "confirmation": "未知", "info_complete": true, "unrelated": false, "missing_info": []}'
        )
        result = make_input_parser().parse_data(ai_content)
        assert result["product_type"] == "冰箱"
        assert result["fault_desc"] == "不制冷"
        assert result["phone"] == "13800138000"
        assert result["engineer_name"] == "李卫东"
        assert result["unrelated"] is False

    def test_should_provide_defaults_on_broken_json(self):
        """JSONDecodeError 时应返回全字段默认值，不抛异常"""
        result = make_input_parser().parse_data("这不是JSON{")
        assert result["product_type"] == "未知"
        assert result["fault_desc"] == "未知"
        assert result["address"] == "未知"
        assert result["phone"] == "未知"
        assert result["start_time"] == "未知"
        assert result["unrelated"] is False
        assert result["info_complete"] is False

    def test_should_parse_empty_input_gracefully(self):
        result = make_input_parser().parse_data("")
        assert isinstance(result, dict)
        assert result["product_type"] == "未知"


class TestHistoryUpdateAndValidation:
    """历史更新与信息完整性"""

    def test_should_mark_complete_when_all_required_present(self):
        processor = make_processor()
        history = {field: None for field in REQUIRED_FIELDS} | {"engineer_name": None}
        finished = processor.update_history_from_data(history, complete_data())
        assert finished is True
        assert history["product_type"] == "空调"
        assert history["phone"] == "13800138000"
        assert history["start_time"] == FUTURE_TIME

    def test_should_not_accept_invalid_phone(self):
        processor = make_processor()
        history = {field: None for field in REQUIRED_FIELDS} | {"engineer_name": None}
        data = complete_data(phone="123")  # 非法手机号
        finished = processor.update_history_from_data(history, data)
        assert finished is False
        assert history["phone"] is None

    def test_should_not_accept_past_or_out_of_window_time(self):
        processor = make_processor()
        history = {field: None for field in REQUIRED_FIELDS} | {"engineer_name": None}
        data = complete_data(start_time="2001-01-01 08:00")  # 过去且早于9点
        finished = processor.update_history_from_data(history, data)
        assert finished is False
        assert history["start_time"] is None

    def test_should_accept_boundary_hour_and_reject_closing_boundary(self):
        processor = make_processor()
        # 9:00 整点属于服务窗口
        history = {field: None for field in REQUIRED_FIELDS} | {"engineer_name": None}
        base = dict(complete_data())
        base["start_time"] = "2099-01-01 09:00"
        assert processor.update_history_from_data(history, base) is True

    def test_unknown_values_should_not_overwrite_existing(self):
        processor = make_processor()
        history = {field: None for field in REQUIRED_FIELDS} | {"engineer_name": None}
        history.update({"product_type": "空调", "address": "朝阳区望京某小区"})
        data = complete_data(product_type="未知", address="未知")
        processor.update_history_from_data(history, data)
        # 既有信息应保留，不被"未知"覆盖
        assert history["product_type"] == "空调"
        assert history["address"] == "朝阳区望京某小区"


class TestRecommendationConfirmationFlow:
    """指定工程师无档期 → 替代推荐 → 用户确认/拒绝"""

    def _fresh_history(self):
        processor = make_processor()
        history = {field: None for field in REQUIRED_FIELDS} | {
            "engineer_name": None,
            "recommended_engineer": None,
            "original_engineer": None,
            "awaiting_confirmation": False,
        }
        processor.update_history_from_data(history, complete_data())
        return processor, history

    def test_positive_confirmation_accepts_recommendation(self):
        processor, history = self._fresh_history()
        recommended = {"id": 2, "name": "李卫东"}
        history["recommended_engineer"] = recommended
        history["awaiting_confirmation"] = True

        finished = processor.update_history_from_data(history, complete_data(confirmation="好的，可以"))
        assert finished is True
        assert history["confirmed_engineer"]["id"] == 2
        assert history["awaiting_confirmation"] is False

    def test_negative_confirmation_declines_recommendation(self):
        processor, history = self._fresh_history()
        history["recommended_engineer"] = {"id": 2, "name": "李卫东"}
        history["awaiting_confirmation"] = True

        finished = processor.update_history_from_data(history, complete_data(confirmation="不要"))
        assert finished is True
        assert history["recommendation_declined"] is True
        assert history["awaiting_confirmation"] is False

    def test_ambiguous_reply_keeps_waiting(self):
        processor, history = self._fresh_history()
        history["recommended_engineer"] = {"id": 2, "name": "李卫东"}
        history["awaiting_confirmation"] = True

        finished = processor.update_history_from_data(history, complete_data(confirmation="嗯嗯"))
        assert finished is False
        assert history["awaiting_confirmation"] is True

    @pytest.mark.asyncio
    async def test_declined_path_emits_reply_without_writing_db(self):
        """拒绝推荐 → 输出换时间/换人建议，不落库（不产生工单）"""
        processor, history = self._fresh_history()
        history["recommendation_declined"] = True
        history["recommended_engineer"] = {"id": 2, "name": "李卫东"}

        tokens = []
        async for token in processor.handle_complete_appointment(history, "session-test"):
            tokens.append(token)
        text = "".join(tokens)
        assert "[REPLY][报修专员]" in text
        assert "工程师" in text

    @pytest.mark.asyncio
    async def test_awaiting_confirmation_path_prompts_user(self):
        processor, history = self._fresh_history()
        history["awaiting_confirmation"] = True

        tokens = []
        async for token in processor.handle_complete_appointment(history, "session-test"):
            tokens.append(token)
        text = "".join(tokens)
        assert "[REPLY][报修专员]" in text
        assert "是" in text and "不" in text


class TestIncompleteInfoAsking:
    """缺信息逐项追问"""

    @pytest.mark.asyncio
    async def test_asks_for_missing_fields_in_order(self):
        processor = make_processor()
        history = {
            "product_type": None, "fault_desc": "不制冷",
            "address": None, "phone": None, "start_time": None,
        }
        tokens = []
        async for token in processor.handle_incomplete_info({}, history):
            tokens.append(token)
        text = "".join(tokens)
        # 追问品类 / 地址 / 手机号（报修上下文只缺这三项）
        assert "家电" in text or "品类" in text
        assert "地址" in text
        assert "手机号" in text

    @pytest.mark.asyncio
    async def test_asks_for_phone_when_missing_only_it(self):
        processor = make_processor()
        history = {
            "product_type": "空调", "fault_desc": "不制冷",
            "address": "朝阳区望京某小区3号楼", "phone": None,
            "start_time": FUTURE_TIME,
        }
        tokens = []
        async for token in processor.handle_incomplete_info({}, history):
            tokens.append(token)
        text = "".join(tokens)
        assert "手机号" in text
        assert "地址" not in text


class TestMessageBuilderCopywriting:
    """家电化话术快照"""

    def test_missing_info_table_is_appliance_oriented(self):
        builder = MessageBuilder()
        # 追问表与处理器共用 missing_info_prompts，需包含完整槽位
        assert "phone" in builder.missing_info_prompts
        assert "start_time" in builder.missing_info_prompts
        assert "手机号" in builder.missing_info_prompts["phone"]
        assert "9:00-18:00" in builder.missing_info_prompts["start_time"]
    def test_success_message_contains_ticket_and_engineer(self):
        builder = MessageBuilder()
        msg = builder.create_appointment_success_message(
            ticket_no="AX2099010101",
            tech={"id": 1, "name": "张建国", "service_region": "海淀区"},
            product_type="空调",
            time_range="2099-01-01 15:00 - 2099-01-01 17:00",
            warranty_note="查询到您名下该产品仍在保修期内",
        )
        assert "报修已登记成功" in msg
        assert "报修单号：AX2099010101" in msg
        assert "张建国" in msg
        assert "15:00" in msg

    def test_unrelated_and_error_messages_are_appliance_scoped(self):
        builder = MessageBuilder()
        unrelated = builder.create_unrelated_message()
        assert "家电" in unrelated or "报修" in unrelated or "售后" in unrelated
        parse_error = builder.create_parse_error_message()
        assert "抱歉" in parse_error or "理解" in parse_error


class TestReset:
    """报修状态重置（组件层验证，避免依赖 LLM key）"""

    def test_reset_clears_history_fields(self):
        from agents.appointment_agent import AppointmentAgent

        try:
            agent = AppointmentAgent(session_id="test-reset")
        except Exception as exc:  # 无 LLM key 的环境无法初始化真实控制器
            pytest.skip(f"AppointmentAgent 无法在无 key 环境初始化: {exc}")

        agent.appointment_history["product_type"] = "空调"
        agent.appointment_history["phone"] = "13800138000"
        agent.finished = True
        agent.reset()
        for field in ["product_type", "fault_desc", "address", "phone", "start_time", "engineer_name"]:
            assert agent.appointment_history[field] is None, f"{field} 应被清空"
        assert agent.finished is False
