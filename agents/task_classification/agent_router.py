"""
智能体路由器 - 专门负责根据分类结果将请求路由到对应的Agent

职责：
1. 接收分类结果，决定使用哪个Agent处理
2. 协调各个Agent之间的调用
3. 管理Agent的初始化和状态同步
4. 提供统一的Agent调用接口
"""

import re
from typing import Any, AsyncGenerator
from .state_manager import StateManager


class AgentRouter:
    """智能体路由器 - 根据任务类型路由到对应的处理Agent"""
    
    def __init__(self, appointment_agent: Any, consultant_agent: Any, state_manager: StateManager):
        """
        初始化路由器
        
        Args:
            appointment_agent: 预约处理Agent
            consultant_agent: 咨询处理Agent
            state_manager: 状态管理器
        """
        self.appointment_agent = appointment_agent
        self.consultant_agent = consultant_agent
        self.state_manager = state_manager
        
        # 设置Agent的共享状态
        self._setup_agent_states()
    
    def _setup_agent_states(self):
        """设置各Agent的共享状态"""
        if self.appointment_agent and hasattr(self.appointment_agent, 'set_shared_state'):
            self.appointment_agent.set_shared_state(self.state_manager.state)
        
        if self.consultant_agent and hasattr(self.consultant_agent, 'set_shared_state'):
            self.consultant_agent.set_shared_state(self.state_manager.state)
    
    async def route_to_appointment(self, task: str) -> AsyncGenerator[str, None]:
        """
        路由到预约Agent处理
        
        Args:
            task: 用户任务内容
            
        Yields:
            str: 流式响应内容
        """
        if not self.appointment_agent:
            yield "[ERROR]报修服务暂时不可用"
            return

        # 转换状态
        self.state_manager.transition_to_appointment()

        # 生成思考提示
        yield "[THOUGHT][客服调度] 客服调度：检测到家电报修/上门预约任务，转给报修专员处理。"

        # 调用报修Agent
        try:
            async for token in self.appointment_agent.run_stream(user_input=task):
                yield token
        except Exception as e:
            yield f"[ERROR]报修处理失败: {str(e)}"
            self.state_manager.reset_to_classify()
    
    async def route_to_consultation(self, task: str) -> AsyncGenerator[str, None]:
        """
        路由到咨询Agent处理
        
        Args:
            task: 用户任务内容
            
        Yields:
            str: 流式响应内容
        """
        if not self.consultant_agent:
            yield "[ERROR]咨询服务暂时不可用"
            return
        
        # 转换状态
        self.state_manager.transition_to_consultation()
        
        # 生成思考提示
        yield "[THOUGHT][客服调度] 客服调度：检测到售后咨询任务，转给售后顾问处理。"
        
        # 调用咨询Agent
        try:
            async with self.consultant_agent as agent:
                async for token in agent.consult_stream(task):
                    yield token
        except Exception as e:
            yield f"[ERROR]咨询处理失败: {str(e)}"
            self.state_manager.reset_to_classify()
    
    async def route_to_complaint(self, task: str) -> AsyncGenerator[str, None]:
        """
        处理投诉/转人工请求：登记转人工记录并回执（单轮处理，随后回到分类状态）

        Args:
            task: 用户任务内容

        Yields:
            str: 流式响应内容
        """
        try:
            yield "[THOUGHT][客服调度] 客服调度：检测到投诉/转人工诉求，登记人工客服记录。"
            reply = self._register_human_handover(task)
            yield "[REPLY][客服调度]"
            for char in reply:
                yield char
        except Exception as e:
            yield f"[ERROR]转人工登记失败: {str(e)}"
        finally:
            # 单轮处理后回到分类状态，等待下一条消息
            self.state_manager.reset_to_classify()

    def _register_human_handover(self, task: str) -> str:
        """登记转人工记录，返回客户回执话术"""
        # 从请求中尝试提取手机号与报修单号
        phone_match = re.search(r"1\d{10}", task)
        ticket_match = re.search(r"AX\d{10,14}", task, re.IGNORECASE)

        ticket_text = ""
        ticket_id = None
        ticket_no = None
        if ticket_match:
            ticket_no = ticket_match.group(0).upper()
            try:
                from services.ticket_service import TicketService
                ticket = TicketService().get_ticket_by_no(ticket_no)
                if ticket:
                    ticket_id = ticket['id']
                    ticket_text = f"，已关联报修单 {ticket_no}"
                else:
                    ticket_text = f"，您提到的报修单 {ticket_no} 未查询到，将一并核实"
            except Exception:
                pass

        issue_summary = re.sub(r"\s+", " ", task)[:200]
        phone = phone_match.group(0) if phone_match else None

        from services.handover_service import HandoverService
        HandoverService().create_handover(
            issue_summary=issue_summary,
            user_phone=phone,
            ticket_id=ticket_id
        )

        if phone:
            return (f"您好，已为您登记转人工处理{ticket_text}，售后专员将在30分钟内回电"
                    f"{phone}为您跟进解决，请留意接听。感谢您的理解与信任！")
        return (f"您好，已为您登记转人工处理{ticket_text}，售后专员将尽快与您联系。"
                f"为确保及时回电，您也可以提供11位手机号。感谢您的理解与信任！")

    async def handle_unsupported_task(self, category: str) -> AsyncGenerator[str, None]:
        """
        处理不支持的任务类型

        Args:
            category: 任务分类结果

        Yields:
            str: 回复内容
        """
        reply = "抱歉，我暂时无法处理这类任务。安居家电售后客服可以协助您：家电报修登记、售后政策咨询、订单保修查询、投诉转人工。请问有什么可以帮您？"
        yield "[REPLY][客服调度]"
        for char in reply:
            yield char
    
    async def route_by_state(self, task: str) -> AsyncGenerator[str, None]:
        """
        根据当前状态路由任务（用于状态持续的场景）
        
        Args:
            task: 用户任务内容
            
        Yields:
            str: 流式响应内容
        """
        if self.state_manager.is_in_appointment_flow():
            async for token in self.appointment_agent.run_stream(user_input=task):
                yield token
        elif self.state_manager.is_in_consultation_flow():
            async with self.consultant_agent as agent:
                async for token in agent.consult_stream(task):
                    yield token
        else:
            # 状态异常，重置并提示
            self.state_manager.reset_to_classify()
            yield "[ERROR]会话状态异常，已重置。请重新开始对话。"
    
    def get_available_services(self) -> list:
        """获取可用的服务列表"""
        services = []
        if self.appointment_agent:
            services.append("报修预约服务")
        if self.consultant_agent:
            services.append("售后咨询服务")
        return services
