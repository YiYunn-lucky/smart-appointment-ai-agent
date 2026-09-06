from dotenv import load_dotenv
import uuid
from typing import Any, Dict
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.messages import AIMessage, HumanMessage
from config.model_provider import create_chat_model
from .appointment import (
    InputParser,
    EngineerFinder,
    AppointmentProcessor,
    MessageBuilder
)

load_dotenv()


class AppointmentAgent:
    """
    报修专员控制器

    职责：
    1. 初始化各个组件
    2. 管理会话状态
    3. 协调整个家电报修登记流程
    """
    
    def __init__(self, session_id=None, unrelated_callback=None):
        # 基础设置
        self.session_id = session_id or str(uuid.uuid4())
        self.unrelated_callback = unrelated_callback
        self.state = None
        
        # 初始化LLM
        self.llm = self._initialize_llm()

        # 初始化组件
        self.input_parser = InputParser(self.llm)
        self.engineer_finder = EngineerFinder()
        self.message_builder = MessageBuilder()
        self.appointment_processor = AppointmentProcessor(
            self.input_parser,
            self.engineer_finder,
            self.message_builder,
            self.llm
        )
        
        # 会话管理
        self.chats_by_session_id = {}
        self.chat_history = self._get_chat_history(self.session_id)
        # 会话上下文（由 AgentSessionRegistry 挂载；非空时窗口/槽位以上下文为准）
        self.session_context = None

        # 预约状态
        self.reset()

    def _initialize_llm(self):
        """初始化通用聊天模型"""
        return create_chat_model(temperature=0)

    def _get_chat_history(self, session_id: str) -> InMemoryChatMessageHistory:
        """获取或创建会话历史记录"""
        chat_history = self.chats_by_session_id.get(session_id)
        if chat_history is None:
            chat_history = InMemoryChatMessageHistory()
            self.chats_by_session_id[session_id] = chat_history
        return chat_history

    # ---------- 会话上下文（分层记忆）模式 ----------

    @property
    def _in_context_mode(self) -> bool:
        """会话上下文模式：历史/槽位由 SessionContext 提供（跨请求、可重启恢复）"""
        return self.session_context is not None

    def _history_from_context(self) -> InMemoryChatMessageHistory:
        """由上下文的最近窗口重建临时解析历史（仅本会话轮次，旧会话事实由槽位而非提示词承载）"""
        history = InMemoryChatMessageHistory()
        for msg in self.session_context.recent_messages():
            history.add_message(HumanMessage(content=msg["content"]) if msg.get("role") == "user"
                                else AIMessage(content=msg["content"]))
        return history

    def _current_slots(self) -> Dict:
        """当前工作槽位字典：上下文模式下直接引用 ctx 槽位（改写即持久化）"""
        return self.session_context.appointment_slots if self._in_context_mode else self.appointment_history

    def reset(self):
        """重置报修历史和状态"""
        self.appointment_history = {
            "product_type": None,
            "fault_desc": None,
            "address": None,
            "phone": None,
            "start_time": None,
            "engineer_name": None
        }
        self.finished = False
        self.chat_history.clear()

    def set_shared_state(self, shared_state):
        """设置共享状态"""
        self.state = shared_state

    async def run_stream(self, user_input=None):
        """
        流式处理用户预约请求的主函数
        
        这是整个预约流程的入口点，协调各个组件完成预约
        """
        if user_input is None:
            user_input = input("用户：")

        # 会话上下文模式：解析历史由窗口重建、槽位直用 ctx（跨请求/重启续谈）
        parse_history = self._history_from_context() if self._in_context_mode else self.chat_history
        slots = self._current_slots()

        # 1. 解析用户输入（内部 JSON，不向用户流式输出，避免英文字段名暴露在聊天界面）
        ai_content = ""
        for token in self.input_parser.parse_stream(user_input, parse_history):
            ai_content += token

        try:
            # 2. 解析AI返回的数据
            data = self.input_parser.parse_data(ai_content)
            self.finished = self.appointment_processor.update_history_from_data(slots, data)

            # 3. 处理与报修无关的请求
            # 如果正在等待用户确认推荐的替换工程师，不要转交给客服调度
            if data.get("unrelated", False) and not slots.get('awaiting_confirmation'):
                # 注意：这里不清空预约历史，保留用户已输入的信息
                # 只设置状态为CLASSIFY，让系统转交给其他机器人处理
                if self.state:
                    from config.constants import StateEnum
                    self.state.value = StateEnum.CLASSIFY

                async for token in self.appointment_processor.handle_unrelated_request(
                    user_input, self.unrelated_callback, self.state
                ):
                    yield token
                return

            # 4. 处理预约完成的情况
            if self.finished:
                recommendation_pending = False
                async for token in self.appointment_processor.handle_complete_appointment(
                    slots, self.session_id
                ):
                    # 检查是否有推荐等待确认
                    if token == "[SIGNAL]recommendation_pending":
                        recommendation_pending = True
                        # 将 finished 设为 False，让预约流程继续
                        self.finished = False
                        continue
                    yield token

                # 只有在真正完成预约时才重置状态
                if not recommendation_pending and not slots.get('awaiting_confirmation'):
                    self._reset_state_after_appointment()
                return

            # 5. 处理信息不完整的情况
            async for token in self.appointment_processor.handle_incomplete_info(data, slots):
                yield token

        except Exception as e:
            yield self.message_builder.create_parse_error_message()

    def _reset_state_after_appointment(self):
        """预约完成后重置状态（上下文模式只清槽位，保留窗口与摘要）"""
        if self._in_context_mode:
            self.session_context.reset_slots()
            self.finished = False
        else:
            self.reset()
        if self.state:
            from config.constants import StateEnum
            self.state.value = StateEnum.CLASSIFY
