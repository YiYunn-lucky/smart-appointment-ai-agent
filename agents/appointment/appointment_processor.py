"""
报修预约处理器

负责协调整个家电报修流程：
1. 从解析数据更新报修历史（产品/故障/地址/手机号/上门时间/指定工程师）
2. 信息不完整时逐项追问（含手机号格式、上门时段校验）
3. 信息完整后匹配工程师（指定/推荐/系统匹配）
4. 生成报修单并派单，落库用户行为与保修提示
"""

import re
from typing import Dict, Any, AsyncGenerator
from .input_parser import InputParser
from .engineer_finder import EngineerFinder
from .message_builder import MessageBuilder

PHONE_PATTERN = re.compile(r"^1\d{10}$")

REQUIRED_FIELDS = ["product_type", "fault_desc", "address", "phone", "start_time"]


class AppointmentProcessor:
    """报修预约处理器"""

    def __init__(self, input_parser: InputParser, engineer_finder: EngineerFinder,
                 message_builder: MessageBuilder, llm=None):
        self.input_parser = input_parser
        self.engineer_finder = engineer_finder
        self.message_builder = message_builder
        self.llm = llm

    # ---------- 字段校验 ----------

    @staticmethod
    def _is_valid_phone(phone: str) -> bool:
        return bool(phone) and phone != "未知" and bool(PHONE_PATTERN.match(phone.strip()))

    @staticmethod
    def _is_valid_start_time(start_time_str: str) -> bool:
        """上门时间需可解析、处于9:00-18:00窗口且不早于当前时间"""
        if not start_time_str or start_time_str == "未知":
            return False
        from config.time_config import time_config
        start_time = time_config.parse_datetime(start_time_str)
        if start_time is None:
            return False
        if start_time < time_config.naive_now():
            return False
        start_hour, end_hour = time_config.get_business_hours()
        return start_hour <= start_time.hour < end_hour

    # ---------- 历史更新与完成判定 ----------

    def update_history_from_data(self, appointment_history: Dict[str, Any], data: Dict[str, Any]) -> bool:
        """从解析数据更新报修历史，返回信息是否已完整"""
        # 检查是否在等待用户确认推荐工程师
        if appointment_history.get('awaiting_confirmation'):
            return self._handle_recommendation_response(appointment_history, data)

        # 只更新有值且通过校验的字段，避免覆盖之前的信息
        for key in ["product_type", "fault_desc", "address", "engineer_name"]:
            if data.get(key) and data[key] != "未知":
                appointment_history[key] = data[key]

        if data.get("phone") and data["phone"] != "未知" and self._is_valid_phone(data["phone"]):
            appointment_history["phone"] = data["phone"]

        if data.get("start_time") and self._is_valid_start_time(data["start_time"]):
            appointment_history["start_time"] = data["start_time"]

        # 必需信息齐全即视为完整（engineer_name 可选，由系统推荐）
        has_all_required = all(
            appointment_history.get(field) and appointment_history[field] != "未知"
            for field in REQUIRED_FIELDS
        )
        return has_all_required

    def _handle_recommendation_response(self, appointment_history: Dict[str, Any], data: Dict[str, Any]) -> bool:
        """处理用户对替代工程师推荐的回应"""
        user_response = str(data.get('confirmation', '')).lower()

        # 判断用户是否同意推荐
        positive_responses = ['是', '好', '可以', '同意', '确定', 'yes', 'ok', '行', '换']
        negative_responses = ['不', '不要', '不行', '不同意', 'no', '别']

        is_positive = any(pos in user_response for pos in positive_responses)
        is_negative = any(neg in user_response for neg in negative_responses)

        if is_positive and not is_negative:
            recommended_tech = appointment_history.get('recommended_engineer')
            if recommended_tech:
                appointment_history['confirmed_engineer'] = recommended_tech
                appointment_history['awaiting_confirmation'] = False
                return True
        elif is_negative:
            appointment_history['recommendation_declined'] = True
            appointment_history['awaiting_confirmation'] = False
            return True

        # 用户回应不明确，继续等待
        return False

    # ---------- 无关请求 ----------

    async def handle_unrelated_request(self, user_input: str, unrelated_callback, state) -> AsyncGenerator[str, None]:
        """处理与报修预约无关的请求（转交归类机器人）"""
        # 注意：这里不重置状态，因为在调用处已经设置了状态
        # 保持报修历史不被清空
        if unrelated_callback:
            try:
                yield "[REPLY][报修专员]和报修预约信息无关，已交给客服调度处理\n"
                result = await unrelated_callback(user_input)
                if hasattr(result, '__aiter__'):
                    async for token in result:
                        yield token
                else:
                    yield result
            except Exception as e:
                yield f"[ERROR]处理请求时发生错误: {str(e)}\n"
                yield self.message_builder.create_unrelated_message()
        else:
            yield self.message_builder.create_unrelated_message()

    # ---------- 完整报修处理 ----------

    async def handle_complete_appointment(self, appointment_history: Dict[str, Any],
                                          session_id: str) -> AsyncGenerator[str, None]:
        """处理报修信息完整的情况"""
        # 检查是否用户拒绝了替代推荐
        if appointment_history.get('recommendation_declined'):
            reply = self.message_builder.create_recommendation_declined_message(self.llm)
            yield f"[REPLY][报修专员]{reply}"
            appointment_history.pop('recommendation_declined', None)
            appointment_history.pop('recommended_engineer', None)
            appointment_history.pop('original_engineer', None)
            return

        # 检查是否用户确认了替代推荐
        if appointment_history.get('confirmed_engineer'):
            tech = appointment_history['confirmed_engineer']
            tech['is_recommendation'] = True
            tech['original_engineer'] = appointment_history.get('original_engineer')
            reply = self._process_successful_repair(tech, appointment_history, session_id)
            yield f"[REPLY][报修专员]{reply}"
            appointment_history.pop('confirmed_engineer', None)
            appointment_history.pop('recommended_engineer', None)
            appointment_history.pop('original_engineer', None)
            return

        # 检查是否在等待用户确认替代推荐
        if appointment_history.get('awaiting_confirmation'):
            yield f"[REPLY][报修专员]\n机器人：请您明确回复\"是\"（同意更换工程师）或\"不\"（重新安排），我好为您登记。\n"
            return

        # 收集思考过程
        thought_msgs = []
        def collect_thoughts(msg):
            thought_msgs.append(msg)

        tech = self.engineer_finder.find_engineer_with_thought(appointment_history, collect_thoughts)

        # 输出所有思考过程
        for msg in thought_msgs:
            yield msg

        engineer_name = appointment_history.get("engineer_name")

        if tech:
            if tech.get('requires_confirmation'):
                original_tech = tech.get('original_engineer')
                recommended_tech = tech.get('recommended_engineer')

                recommendation_msg = self.message_builder.create_engineer_recommendation_message(
                    original_tech, recommended_tech, appointment_history, self.llm
                )
                yield f"[REPLY][报修专员]{recommendation_msg}"

                appointment_history['recommended_engineer'] = recommended_tech
                appointment_history['original_engineer'] = original_tech
                appointment_history['awaiting_confirmation'] = True

                # 告诉调用方报修尚未真正完成，需要继续等待用户输入
                yield "[SIGNAL]recommendation_pending"
                return
            else:
                reply = self._process_successful_repair(tech, appointment_history, session_id)
                yield f"[REPLY][报修专员]{reply}"
        else:
            reply = self.message_builder.create_appointment_failure_message(engineer_name)
            yield f"[REPLY][报修专员]{reply}"

    def _process_successful_repair(self, tech: Dict[str, Any], appointment_history: Dict[str, Any],
                                   session_id: str) -> str:
        """生成报修工单并派单，返回确定性成功消息（含保修状态提示）"""
        start_time, end_time, _ = self.engineer_finder.parse_repair_time(appointment_history["start_time"])
        if not start_time or not end_time:
            return self.message_builder.create_appointment_failure_message(
                appointment_history.get("engineer_name"))

        # 1. 创建报修工单（pending）并派单（写工程师忙档、置 assigned）
        from services.ticket_service import TicketService
        ticket_service = TicketService()
        ticket = ticket_service.create_ticket(
            user_name=None,
            user_phone=appointment_history["phone"],
            product_type=appointment_history["product_type"],
            fault_desc=appointment_history["fault_desc"],
            address=appointment_history["address"],
            start_time=start_time,
            end_time=end_time,
            engineer_id=tech["id"]
        )
        if not ticket:
            return self.message_builder.create_save_failure_message()

        assigned = ticket_service.assign_ticket(ticket["id"], tech["id"]) if ticket['status'] == 'pending' else ticket
        if not assigned:
            return self.message_builder.create_save_failure_message()

        # 2. 记录用户行为（客户=手机号）
        try:
            from services.user_behavior_service import UserBehaviorService
            UserBehaviorService().record_behavior(
                user_id=appointment_history["phone"],
                action_type='repair',
                action_data={
                    'product_type': appointment_history["product_type"],
                    'fault_desc': appointment_history["fault_desc"],
                    'address': appointment_history["address"],
                    'start_time': start_time.strftime("%Y-%m-%d %H:%M"),
                    'end_time': end_time.strftime("%Y-%m-%d %H:%M"),
                    'engineer_id': tech["id"],
                    'ticket_no': assigned['ticket_no']
                },
                engineer_id=tech["id"],
                session_id=session_id
            )
        except Exception as e:
            print(f"记录报修行为失败（不影响报修成功）：{e}")

        # 3. 保修状态提示（匹配演示订单）
        warranty_note = self._build_warranty_note(appointment_history["phone"],
                                                  appointment_history["product_type"])

        from config.time_config import time_config
        time_range = f"{time_config.format_datetime(start_time)} - {time_config.format_datetime(end_time)}"
        return self.message_builder.create_appointment_success_message(
            ticket_no=assigned['ticket_no'],
            tech=tech,
            product_type=appointment_history["product_type"],
            time_range=time_range,
            warranty_note=warranty_note
        )

    def _build_warranty_note(self, phone: str, product_type: str) -> str:
        """按手机号查询名下该品类订单，返回保修状态提示（无记录返回空串）"""
        try:
            from services.order_service import OrderService
            orders = OrderService().get_warranty_info(phone)
            matched = [o for o in orders if o['product_type'] == product_type]
            if not matched:
                return ""
            order = matched[0]
            if order['in_warranty']:
                end_date = order['warranty_end'].strftime("%Y-%m-%d") if order['warranty_end'] else ""
                return (f"查询到您名下该产品仍在保修期内（保修至{end_date}），本次上门维修免收上门费和维修费；"
                        f"如需更换配件将单独告知费用。")
            return "该产品已超出保修期，本次上门将按标准收取上门检测费与维修费，工程师会先报价再维修。"
        except Exception as e:
            print(f"生成保修提示失败: {e}")
            return ""

    # ---------- 信息不完整 ----------

    async def handle_incomplete_info(self, data: Dict[str, Any], appointment_history: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """处理信息不完整的情况：逐项追问缺失字段"""
        missing = []
        for field in REQUIRED_FIELDS:
            value = appointment_history.get(field)
            if not value or value == "未知":
                if field == "phone" and not self._is_valid_phone(value or ""):
                    missing.append("phone")
                elif field == "start_time" and not self._is_valid_start_time(value or ""):
                    missing.append("start_time")
                else:
                    missing.append(field)

        reply = self.message_builder.create_missing_info_questions(missing)
        yield f"[THOUGHT][报修专员]用户的报修信息不完整，缺少：{', '.join(missing)}，需要询问用户补充"
        yield f"[REPLY][报修专员]{reply}"
