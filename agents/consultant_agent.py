import uuid
from config.model_provider import create_chat_model
from .consultant import (
    KnowledgeRetriever,
    ConsultationClassifier,
    ResponseGenerator,
    ConsultationProcessor
)


class ConsultantAgent:
    """
    售后顾问主控制器

    职责：
    1. 初始化各个组件
    2. 管理会话状态
    3. 协调整个售后咨询流程
    """
    
    def __init__(self, session_id=None):
        # 基础设置
        self.session_id = session_id or str(uuid.uuid4())
        self.shared_state = None
        self.unrelated_callback = None
        # 会话上下文（由 AgentSessionRegistry 挂载；非空时注入客户背景并沉淀长期记忆）
        self.session_context = None
        
        # 初始化LLM
        self.llm = self._initialize_llm()
        
        # 初始化组件
        self.knowledge_retriever = KnowledgeRetriever()
        self.consultation_classifier = ConsultationClassifier(self.llm)
        self.response_generator = ResponseGenerator(self.llm)
        self.consultation_processor = ConsultationProcessor(
            self.knowledge_retriever,
            self.consultation_classifier,
            self.response_generator
        )

    def _initialize_llm(self):
        """初始化通用聊天模型"""
        return create_chat_model(temperature=0.3)

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.knowledge_retriever.initialize()
        print("售后顾问已启动（数据库RAG模式）")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """异步上下文管理器出口"""
        pass

    def set_shared_state(self, shared_state):
        """设置共享状态"""
        self.shared_state = shared_state

    def set_unrelated_callback(self, callback):
        """设置处理非相关任务的回调函数"""
        self.unrelated_callback = callback

    async def consult(self, user_input: str) -> str:
        """
        基础咨询功能
        
        用于非流式的简单咨询场景
        """
        return await self.consultation_processor.process_consultation(user_input)

    async def consult_stream(self, user_input: str):
        """
        流式输出咨询结果

        这是主要的咨询入口点，协调各个组件完成咨询流程
        """
        # 会话上下文：客户背景（滚动摘要+召回 Top-5）注入生成提示，咨询后沉淀长期记忆
        ctx = self.session_context
        background = ctx.background_text(getattr(ctx, 'recalled', None)) if ctx is not None else ""

        # 0. 工单进度/订单保修等查询意图先查库答复：命中即结束，
        #    避免"查询报修单进度"等请求在售后顾问二次分类中被误判为无关而转回死循环
        if self.consultation_processor.try_lookup(user_input):
            async for token in self.consultation_processor.process_consultation_stream(
                user_input, self.session_id, background
            ):
                yield token
            self._record_consultation_memory(user_input)
            self._reset_state_after_consultation()
            return

        # 1. 检查是否与咨询相关
        is_consultation = await self.consultation_classifier.is_consultation_related(user_input)

        if not is_consultation:
            # 2. 处理与咨询无关的请求
            async for token in self.consultation_processor.handle_unrelated_request(
                user_input, self.unrelated_callback, self.shared_state
            ):
                yield token
            return

        # 3. 处理咨询相关的请求
        async for token in self.consultation_processor.process_consultation_stream(
            user_input, self.session_id, background
        ):
            yield token

        # 4. 沉淀长期记忆并重置状态
        self._record_consultation_memory(user_input)
        self._reset_state_after_consultation()

    def _record_consultation_memory(self, user_input: str):
        """咨询完成后把问题要点写入长期记忆（仅已绑定客户；失败不影响回复）"""
        ctx = self.session_context
        if ctx is None or not ctx.user_id:
            return
        try:
            from services.memory_service import MemoryService
            MemoryService().add_memory(
                user_id=ctx.user_id,
                content=f"咨询：{user_input[:100]}",
                memory_type='consult',
                source_session_id=self.session_id,
            )
        except Exception as e:
            print(f"记录咨询长期记忆失败（不影响回复）：{e}")

    def _reset_state_after_consultation(self):
        """咨询完成后重置状态"""
        if self.shared_state:
            from config.constants import StateEnum
            self.shared_state.value = StateEnum.CLASSIFY
