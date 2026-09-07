"""
咨询流程处理器

负责协调整个咨询流程：
1. 命中订单保修/报修工单进度等查询意图时，直接查库给出模板化答复
2. 其他售后问题走知识库RAG检索 + LLM生成
"""

import re
from typing import AsyncGenerator, Optional
from .knowledge_retriever import KnowledgeRetriever
from .consultation_classifier import ConsultationClassifier
from .response_generator import ResponseGenerator


class ConsultationProcessor:
    """咨询流程处理器"""

    TICKET_PATTERN = re.compile(r"AX\d{10,14}", re.IGNORECASE)
    PHONE_PATTERN = re.compile(r"1\d{10}")
    ORDER_INTENT_KEYWORDS = ("保修", "在保", "过保", "超保", "质保", "出保", "保内", "保外", "订单", "还保")
    # 报修工单类意图词（先于保修词判定："报修订单/维修单"含"订单"却指工单）
    TICKET_INTENT_KEYWORDS = ("报修单", "维修单", "报修订单", "维修订单", "工单", "进度", "单号")
    TICKET_STATUS_LABELS = {
        'pending': '待派单',
        'assigned': '已派单，工程师将联系您',
        'in_progress': '维修中',
        'completed': '已完成',
        'cancelled': '已取消'
    }

    def __init__(self, knowledge_retriever: KnowledgeRetriever,
                 consultation_classifier: ConsultationClassifier,
                 response_generator: ResponseGenerator):
        self.knowledge_retriever = knowledge_retriever
        self.consultation_classifier = consultation_classifier
        self.response_generator = response_generator

    async def process_consultation(self, user_input: str) -> str:
        """处理标准咨询"""
        # 0. 订单保修/工单进度等查询意图直接查库答复
        lookup_answer = self._try_lookup_answer(user_input)
        if lookup_answer:
            return lookup_answer

        # 1. 检索知识
        knowledge_docs = await self.knowledge_retriever.search_knowledge(user_input, top_k=3)

        # 2. 生成响应
        response = await self.response_generator.generate_response(user_input, knowledge_docs)

        return response

    async def process_consultation_stream(self, user_input: str, session_id: str,
                                          background: str = "",
                                          user_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """处理流式咨询（background 为客户档案/历史要点，RAG 生成时透传；
        user_id 为已绑定客户手机号，咨询行为按客户落库供离线沉淀）"""
        try:
            # 0. 订单保修/工单进度等查询意图直接查库答复
            lookup_answer = self._try_lookup_answer(user_input)
            if lookup_answer:
                yield "[REPLY][售后顾问]"
                for char in lookup_answer:
                    yield char
                await self._record_consultation_behavior(user_input, [], session_id, user_id)
                return

            # 1. 检索知识
            knowledge_docs = await self.knowledge_retriever.search_knowledge(user_input, top_k=3)

            # 2. 生成响应
            async for token in self.response_generator.generate_response_stream(
                    user_input, knowledge_docs, background):
                yield token

            # 3. 记录用户行为
            await self._record_consultation_behavior(user_input, knowledge_docs, session_id, user_id)

        except Exception as e:
            yield f"[REPLY][售后顾问]抱歉，处理您的问题时出现了错误：{str(e)}"

    async def handle_unrelated_request(self, user_input: str, unrelated_callback, shared_state) -> AsyncGenerator[str, None]:
        """处理与咨询无关的请求"""
        # 重置状态
        if shared_state:
            from config.constants import StateEnum
            shared_state.value = StateEnum.CLASSIFY

        yield self.response_generator.create_unrelated_message()

        # 转给回调处理
        if unrelated_callback:
            async for token in unrelated_callback(user_input):
                yield token

    # ===========================================
    # 订单/工单/保修查询（纯函数，不依赖LLM）
    # ===========================================

    def try_lookup(self, user_input: str):
        """公开的查询意图探测：命中订单/工单查询返回答复文本，否则返回 None"""
        return self._try_lookup_answer(user_input)

    def _try_lookup_answer(self, user_input: str):
        """尝试按查询意图直接查库答复；非查询意图返回None走RAG"""
        try:
            ticket_no = self._extract_ticket_no(user_input)
            if ticket_no:
                return self._build_ticket_status_answer(ticket_no)

            phone = self._extract_phone(user_input)
            if phone:
                # 报修工单类意图（"报修订单/报修单/工单进度"等）→ 查名下工单；须先于保修词判定
                if any(keyword in user_input for keyword in self.TICKET_INTENT_KEYWORDS):
                    return self._build_tickets_answer(phone)
                if any(keyword in user_input for keyword in self.ORDER_INTENT_KEYWORDS):
                    return self._build_warranty_answer(phone)
                # 消息仅含手机号（客户在查询语境下直接发号）：按名下订单+报修记录给出总览
                if self._is_phone_only_message(user_input, phone):
                    return self._build_phone_overview_answer(phone)

            return None
        except Exception as e:
            print(f"查询意图处理失败（走RAG兜底）：{e}")
            return None

    @staticmethod
    def _is_phone_only_message(user_input: str, phone: str) -> bool:
        """判断消息去掉手机号后是否不含其他业务语义（仅少量助词/标点）"""
        remain = re.sub(r"1\d{10}", " ", user_input)
        remain = re.sub(r"[\s\d\W_]", "", remain)
        remain = re.sub(r"我|你|的|了|是|在|请|帮|查|看|下|一|就|这|那|给|找|报|修|单|号|么|吗|呢", "", remain)
        return len(remain) <= 1

    def _extract_ticket_no(self, user_input: str):
        """提取报修单号（AX开头，可能不带AX）"""
        match = self.TICKET_PATTERN.search(user_input)
        if match:
            return match.group(0).upper()
        # 容忍用户只说数字单号：形如20260903001开头的14位以内纯数字，且语句含工单/进度/单号意图
        if any(keyword in user_input for keyword in self.TICKET_INTENT_KEYWORDS):
            digits_match = re.search(r"\d{11,14}", user_input)
            if digits_match and not self.PHONE_PATTERN.fullmatch(digits_match.group(0)):
                return f"AX{digits_match.group(0)}"
        return None

    def _extract_phone(self, user_input: str):
        """提取11位手机号"""
        match = self.PHONE_PATTERN.search(user_input)
        return match.group(0) if match else None

    def _build_ticket_status_answer(self, ticket_no: str) -> str:
        """按报修单号查询工单状态并生成答复"""
        from services.ticket_service import TicketService
        ticket = TicketService().get_ticket_by_no(ticket_no)

        if not ticket:
            return (f"抱歉，未查询到报修单 {ticket_no} 的信息，请您核对单号（如 AX20260903001），"
                    f"或拨打售后热线400-820-9000为您人工查询。")

        status = ticket.get('status')
        status_label = self.TICKET_STATUS_LABELS.get(status, status or '未知状态')
        product = ticket.get('product_type') or '家电'
        fault = ticket.get('fault_desc') or ''

        parts = [f"您的报修单 {ticket_no}（{product}"
                 + (f"，故障：{fault[:30]}" if fault else "")
                 + f"）当前状态：{status_label}。"]

        if ticket.get('engineer_name'):
            parts.append(f"服务工程师：{ticket['engineer_name']}。")
        if ticket.get('start_time'):
            from config.time_config import TimeConfig
            parts.append(f"预约上门时间：{TimeConfig.format_datetime(ticket['start_time'])}。")
        if status in ('completed', 'cancelled') and ticket.get('closed_at'):
            from config.time_config import TimeConfig
            parts.append(f"处理完成时间：{TimeConfig.format_datetime(ticket['closed_at'])}。")

        parts.append("如需其他帮助，随时告诉我。")
        return "".join(parts)

    def _build_tickets_answer(self, phone: str) -> str:
        """按手机号查询名下报修工单列表并生成答复"""
        from services.ticket_service import TicketService
        tickets = TicketService().list_tickets(phone=phone)

        if not tickets:
            return (f"未查询到手机号 {phone} 名下的报修工单。如确有报修记录，请核对手机号或提供报修单号"
                    f"（如 AX20260903001），也可拨打售后热线400-820-9000为您人工查询。")

        lines = [f"手机号 {phone} 名下共查询到 {len(tickets)} 条报修工单："]
        for ticket in tickets:
            status_label = self.TICKET_STATUS_LABELS.get(
                ticket.get('status'), ticket.get('status') or '未知状态')
            fault = ticket.get('fault_desc') or ''
            lines.append(f"· {ticket['ticket_no']}（{ticket.get('product_type') or '家电'}"
                         + (f"，故障：{fault[:20]}" if fault else "")
                         + f"）：{status_label}。")

        lines.append("如需查看某条工单的预约时间或工程师信息，提供对应单号即可，随时告诉我。")
        return "\n".join(lines)

    def _build_warranty_answer(self, phone: str) -> str:
        """按手机号查询名下订单保修状态并生成答复"""
        from services.order_service import OrderService
        orders = OrderService().get_warranty_info(phone)

        if not orders:
            return (f"未查询到手机号 {phone} 名下的购买订单。如确有购买记录，请核对手机号或提供订单号，"
                    f"也可拨打售后热线400-820-9000为您查询。")

        lines = [f"手机号 {phone} 名下共查询到 {len(orders)} 条订单："]
        for order in orders:
            product = order['product_type']
            model = order['brand_model'] or ''
            purchase = order['purchase_date']
            purchase_str = purchase.strftime('%Y年%m月%d日') if purchase else ''
            end = order['warranty_end']
            end_str = end.strftime('%Y年%m月%d日') if end else ''

            if order['in_warranty']:
                lines.append(f"· {product}（{model}，{purchase_str}购买）：在保修期内，保修至{end_str}"
                             + (f"，剩余约{order['days_left']}天。" if order.get('days_left') else "。"))
            else:
                lines.append(f"· {product}（{model}，{purchase_str}购买）：已超出保修期（保修至{end_str}），"
                             f"可安排付费维修，上门检测费50元/次。")

        lines.append("如需了解具体维修费用或预约工程师上门，随时告诉我。")
        return "\n".join(lines)

    def _build_phone_overview_answer(self, phone: str) -> str:
        """消息仅含手机号：按名下订单保修状态与报修工单进度给出总览答复"""
        from services.order_service import OrderService
        from services.ticket_service import TicketService

        orders = OrderService().get_warranty_info(phone)
        tickets = TicketService().list_tickets(phone=phone)

        if not orders and not tickets:
            return (f"未查询到手机号 {phone} 名下的购买订单或报修记录。如确有购买或报修，请核对手机号，"
                    f"或拨打售后热线400-820-9000为您人工查询。")

        parts = [f"已为您查询手机号 {phone} 名下的记录。"]
        if orders:
            order_lines = [f"购买订单共 {len(orders)} 条："]
            for order in orders:
                model = order['brand_model'] or ''
                purchase = order['purchase_date']
                purchase_str = purchase.strftime('%Y年%m月%d日') if purchase else ''
                end = order['warranty_end']
                end_str = end.strftime('%Y年%m月%d日') if end else ''
                if order['in_warranty']:
                    order_lines.append(
                        f"· {order['product_type']}（{model}，{purchase_str}购买）：在保修期内，保修至{end_str}"
                        + (f"，剩余约{order['days_left']}天。" if order.get('days_left') else "。"))
                else:
                    order_lines.append(
                        f"· {order['product_type']}（{model}，{purchase_str}购买）：已超出保修期（保修至{end_str}），"
                        f"可安排付费维修。")
            parts.append("\n".join(order_lines))

        if tickets:
            ticket_lines = [f"报修工单共 {len(tickets)} 条："]
            for ticket in tickets:
                status_label = self.TICKET_STATUS_LABELS.get(
                    ticket.get('status'), ticket.get('status') or '未知状态')
                fault = ticket.get('fault_desc') or ''
                ticket_lines.append(f"· {ticket['ticket_no']}（{ticket.get('product_type') or '家电'}"
                                    + (f"，故障：{fault[:20]}" if fault else "")
                                    + f"）：{status_label}。")
            parts.append("\n".join(ticket_lines))

        parts.append("如需了解保修详情、报修进度或预约工程师上门，随时告诉我。")
        return "\n".join(parts)

    async def _record_consultation_behavior(self, user_input: str, knowledge_docs: list,
                                            session_id: str, user_id: Optional[str] = None):
        """记录咨询行为（已绑定客户时按手机号归属，供 AutoDream 离线沉淀）"""
        try:
            from agents.user_behavior_agent import UserBehaviorAgent
            behavior_agent = UserBehaviorAgent()

            action_data = {
                'question': user_input,
                'knowledge_docs_used': len(knowledge_docs),
                'categories': list(set(doc.get('category', 'unknown') for doc in knowledge_docs)) if knowledge_docs else []
            }

            behavior_agent.record_behavior(
                action_type='consultation',
                action_data=action_data,
                session_id=session_id,
                user_id=user_id or 'default_user'
            )

        except Exception as behavior_error:
            print(f"记录咨询行为失败：{behavior_error}")
