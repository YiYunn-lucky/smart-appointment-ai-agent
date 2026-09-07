"""
分类流程处理器 - 协调整个任务分类流程

职责：
1. 协调任务分类器、状态管理器、路由器等组件
2. 实现完整的分类处理流程
3. 处理异常情况和边界场景
4. 提供统一的流程入口
"""

from typing import Any, AsyncGenerator, Callable, Dict, List, Optional
from .task_classifier import TaskClassifier
from .state_manager import StateManager
from .agent_router import AgentRouter
from .unrelated_handler import UnrelatedHandler
from agents.supervisor.tool_registry import SupervisorToolRegistry, ToolSpec


class ClassificationProcessor:
    """分类流程处理器 - 协调完整的任务分类和处理流程（主管 ReAct：观察→规划→执行→再观察）"""

    def __init__(self,
                 task_classifier: TaskClassifier,
                 state_manager: StateManager,
                 agent_router: AgentRouter,
                 unrelated_handler: UnrelatedHandler,
                 tool_registry: Optional[SupervisorToolRegistry] = None,
                 audit_logger: Optional[Callable[..., Any]] = None):
        """
        初始化分类流程处理器

        Args:
            task_classifier: 任务分类器
            state_manager: 状态管理器
            agent_router: 智能体路由器
            unrelated_handler: 无关请求处理器
            tool_registry: 主管工具注册表（缺省默认工具集；工具与分类枚举一一对应）
            audit_logger: 审计回调（M15）；每次工具选择落一条 scene='supervisor' 审计
        """
        self.task_classifier = task_classifier
        self.state_manager = state_manager
        self.agent_router = agent_router
        self.unrelated_handler = unrelated_handler
        self.tool_registry = tool_registry or SupervisorToolRegistry()
        self.audit_logger = audit_logger
        # 主管规划复盘：每轮回合记录 (category, tool_id)，供审计与测试断言
        self.plan_log: List[Dict[str, str]] = []
        # 单条消息内"子任务转回再分类"的轮次计数：售后顾问等子任务误判转回时防止无限递归
        self._classify_rounds = 0

    def _audit_tool_select(self, tool: ToolSpec, category: str) -> None:
        """主管工具选择审计（谁被选中、风险分级、原因），审计失败不影响规划"""
        if self.audit_logger is None:
            return
        try:
            self.audit_logger(
                actor='supervisor',
                scene='supervisor',
                action='tool_select',
                resource_type='tool',
                resource_id=tool.tool_id,
                tool_id=tool.tool_id,
                risk_tier=tool.risk_tier,
                result='ok',
                detail=f"category={category} 选中{tool.tool_id}",
            )
        except Exception:
            # 审计异常静默：主管规划复盘已入内存 plan_log，不阻断路由
            pass

    # ---------- 主管规划：工具选择与执行 ----------

    def select_tool(self, category: str) -> ToolSpec:
        """主管规划：分类结果 → 选定工具（未知类别自动落到兜底工具），落审计"""
        tool = self.tool_registry.by_category(category)
        self.plan_log.append({"category": category, "tool_id": tool.tool_id})
        self._audit_tool_select(tool, category)
        return tool

    def _invoke_tool(self, tool: ToolSpec, task: str) -> AsyncGenerator[str, None]:
        """主管执行：把选中的工具落到对应处理方（handler 即子 Agent / 确定性流程）"""
        handler = tool.handler
        if handler == "appointment" and self.agent_router.appointment_agent:
            return self.agent_router.route_to_appointment(task)
        if handler == "consultation" and self.agent_router.consultant_agent:
            return self.agent_router.route_to_consultation(task)
        if handler == "complaint":
            return self.agent_router.route_to_complaint(task)
        # fallback / 处理方缺失：能力清单兜底（与原分类链路的 else 分支等价）
        return self.agent_router.handle_unsupported_task(tool.category)
    
    async def process_task_stream(self, task: str) -> AsyncGenerator[str, None]:
        """
        流式处理任务分类和路由
        
        Args:
            task: 用户输入的任务内容
            
        Yields:
            str: 流式响应内容
        """
        try:
            # 检查是否需要进行分类
            if self.state_manager.should_classify():
                # 子任务转回反复分类（如售后顾问对同一请求误判为无关）超过上限时兜底，
                # 避免客服调度 ↔ 子任务间无限递归
                self._classify_rounds += 1
                if self._classify_rounds > 3:
                    self._classify_rounds = 0
                    self.state_manager.reset_to_classify()
                    async for token in self.agent_router.handle_unsupported_task('other'):
                        yield token
                    return

                # 进行任务分类（主管观察：LLM 返回类别）
                category = await self.task_classifier.classify_task(task)

                # 主管规划：按分类结果从工具注册表选择工具并执行
                tool = self.select_tool(category)
                async for token in self._invoke_tool(tool, task):
                    yield token
                # 本条消息的路由链正常结束（未发生转回递归）→ 重置轮次计数
                self._classify_rounds = 0
            else:
                # 根据当前状态继续处理
                async for token in self.agent_router.route_by_state(task):
                    yield token
                    
        except Exception as e:
            # 处理异常情况
            yield f"[ERROR]处理任务时发生错误: {str(e)}"
            self.state_manager.force_reset()
    
    async def process_task_sync(self, task: str) -> str:
        """
        同步处理任务分类和路由（非流式）
        
        Args:
            task: 用户输入的任务内容
            
        Returns:
            str: 处理结果
        """
        try:
            # 检查是否需要进行分类
            if self.state_manager.should_classify():
                category = await self.task_classifier.classify_task(task)
                tool = self.select_tool(category)

                if tool.handler == "appointment" and self.agent_router.appointment_agent:
                    self.state_manager.transition_to_appointment()
                    return await self.agent_router.appointment_agent.run(user_input=task)
                if tool.handler == "consultation" and self.agent_router.consultant_agent:
                    self.state_manager.transition_to_consultation()
                    async with self.agent_router.consultant_agent as agent:
                        return await agent.consult(task)
                if tool.handler == "complaint":
                    # 投诉/转人工：单轮登记处理
                    result = ""
                    async for token in self.agent_router.route_to_complaint(task):
                        result += token
                    return result
                return "抱歉，我暂时无法处理这类任务。安居家电售后客服可以协助您：家电报修登记、售后政策咨询、订单保修查询、投诉转人工。"
            else:
                # 根据当前状态继续处理
                if self.state_manager.is_in_appointment_flow():
                    return await self.agent_router.appointment_agent.run(user_input=task)
                elif self.state_manager.is_in_consultation_flow():
                    async with self.agent_router.consultant_agent as agent:
                        return await agent.consult(task)
                
        except Exception as e:
            self.state_manager.force_reset()
            return f"处理任务时发生错误: {str(e)}"
    
    def get_current_state_info(self) -> dict:
        """获取当前处理状态信息"""
        return {
            'current_state': self.state_manager.get_current_state(),
            'state_description': self.state_manager.get_state_description(),
            'available_services': self.agent_router.get_available_services(),
            'can_classify': self.state_manager.should_classify()
        }
    
    def reset_conversation(self) -> None:
        """重置对话状态"""
        self.state_manager.force_reset()
        self.unrelated_handler.reset_reply_rotation()
    
    async def handle_unrelated_request(self, user_input: str, async_mode: bool = True):
        """
        处理无关请求
        
        Args:
            user_input: 用户输入
            async_mode: 是否使用异步模式
            
        Returns:
            异步模式返回AsyncGenerator，同步模式返回str
        """
        if async_mode:
            async for token in self.unrelated_handler.handle_unrelated_async(user_input):
                yield token
        else:
            # 对于同步模式，我们需要单独处理
            result = await self.unrelated_handler.handle_unrelated_sync(user_input)
            yield result
