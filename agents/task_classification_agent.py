from dotenv import load_dotenv
from typing import Any, Callable, Optional
from config.model_provider import create_chat_model
from config.constants import SharedState
from .supervisor import SupervisorToolRegistry
from .task_classification import (
    TaskClassifier,
    StateManager,
    AgentRouter,
    UnrelatedHandler,
    ClassificationProcessor
)

load_dotenv()


class TaskClassificationAgent:
    """
    任务分类代理主控制器

    职责：
    1. 初始化各个分类组件
    2. 提供统一的任务分类接口
    3. 管理与其他Agent的协调
    """

    def __init__(self, appointment_agent, consultant_agent,
                 audit_logger: Optional[Callable[..., Any]] = None):
        # 基础设置
        self.appointment_agent = appointment_agent
        self.consultant_agent = consultant_agent
        self.audit_logger = audit_logger

        # 初始化LLM（主管分类属高频结构化短调用 → fast 小模型通道，未配置时透明回退 main）
        self.llm = self._initialize_llm()

        # 主管工具注册表：子 Agent / 确定性处理方登记为工具（分类即工具选择）
        self.tool_registry = SupervisorToolRegistry()

        # 初始化组件
        self.state_manager = StateManager(SharedState())
        self.task_classifier = TaskClassifier(self.llm, tools_text=self.tool_registry.manifest_text())
        self.agent_router = AgentRouter(
            appointment_agent,
            consultant_agent,
            self.state_manager,
            audit_logger=audit_logger
        )
        self.unrelated_handler = UnrelatedHandler(self.state_manager)
        self.classification_processor = ClassificationProcessor(
            self.task_classifier,
            self.state_manager,
            self.agent_router,
            self.unrelated_handler,
            tool_registry=self.tool_registry,
            audit_logger=audit_logger
        )
        
        # 设置回调函数
        self._setup_callbacks()
        
        # 保持向后兼容的state属性
        self.state = self.state_manager.state

    def _initialize_llm(self):
        """初始化主管分类模型：fast 通道（高频结构化判断，快且省）"""
        return create_chat_model(temperature=0, tier="fast")
    
    def _setup_callbacks(self):
        """设置Agent的回调函数"""
        if self.appointment_agent and hasattr(self.appointment_agent, 'unrelated_callback'):
            self.appointment_agent.unrelated_callback = self.handle_unrelated
        
        if self.consultant_agent and hasattr(self.consultant_agent, 'set_unrelated_callback'):
            self.consultant_agent.set_unrelated_callback(self.handle_unrelated_async)

    # ===========================================
    # 主要接口方法 - 保持与原版本的兼容性
    # ===========================================
    
    async def classify_task(self, task):
        """分类任务（向后兼容方法）"""
        return await self.classification_processor.process_task_sync(task)

    async def classify_task_stream(self, task):
        """流式分类任务（主要入口）"""
        async for token in self.classification_processor.process_task_stream(task):
            yield token

    def _arm_suppressions(self, armed: bool) -> None:
        """unrelated 转回防抖：转回 supervisor 重分类时，若再次命中同一子 Agent 则不再二次转回"""
        if self.appointment_agent and hasattr(self.appointment_agent, '_suppress_unrelated_once'):
            self.appointment_agent._suppress_unrelated_once = armed
        if self.consultant_agent and hasattr(self.consultant_agent, '_suppress_not_consultation_once'):
            self.consultant_agent._suppress_not_consultation_once = armed

    async def handle_unrelated(self, user_input):
        """处理无关请求（同步版本）"""
        # 与当前子任务无关的请求应该重新进行分类，而不是直接拒绝
        print(f"[DEBUG] 子任务机器人转交的请求：{user_input}")

        # 重新进行任务分类
        self._arm_suppressions(True)
        try:
            result = ""
            async for token in self.classification_processor.process_task_stream(user_input):
                result += token
            return result
        finally:
            self._arm_suppressions(False)

    async def handle_unrelated_async(self, user_input):
        """处理无关请求（异步流版本）"""
        # 与当前子任务无关的请求应该重新进行分类，而不是直接拒绝
        print(f"[DEBUG] 子任务机器人转交的请求：{user_input}")

        # 重新进行任务分类
        self._arm_suppressions(True)
        try:
            async for token in self.classification_processor.process_task_stream(user_input):
                yield token
        finally:
            self._arm_suppressions(False)

    # ===========================================
    # 扩展功能方法
    # ===========================================
    
    def get_classification_info(self):
        """获取分类系统信息"""
        return self.classification_processor.get_current_state_info()
    
    def reset_conversation(self):
        """重置对话状态"""
        self.classification_processor.reset_conversation()
    
    def set_business_context(self, service_name: str = "安居家电售后服务"):
        """设置业务上下文"""
        self.unrelated_handler.set_business_context(service_name)
