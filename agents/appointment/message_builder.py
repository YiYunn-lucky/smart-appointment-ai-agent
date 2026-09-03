"""
消息构建器

负责构建家电报修流程中的各种响应消息
"""

from typing import Dict, Any, List


class MessageBuilder:
    """消息构建器"""

    def __init__(self):
        self.missing_info_prompts = {
            "product_type": "请问是哪种家电出现了故障呢？（空调/冰箱/洗衣机/热水器/净水器/烟灶）",
            "fault_desc": "请简单描述一下故障现象（比如不制冷、漏水、异响），我好安排合适的工程师。",
            "address": "请问上门维修的地址是哪里？麻烦提供城区和详细地址（如：朝阳区望京某小区3号楼），我好为您匹配合适的工程师。",
            "phone": "麻烦提供一下您的11位手机号，工程师上门前会电话与您确认。",
            "start_time": "请问希望工程师几点上门？服务时间为每天9:00-18:00，建议预留2小时维修窗口（比如下午3点上门）。"
        }

    def create_appointment_success_message(self, ticket_no: str, tech: Dict[str, Any],
                                           product_type: str, time_range: str,
                                           warranty_note: str = "") -> str:
        """创建报修登记成功消息"""
        note = f"\n{warranty_note}" if warranty_note else ""
        return (f"\n机器人：您的报修已登记成功！\n"
                f"- 报修单号：{ticket_no}\n"
                f"- 产品：{product_type}\n"
                f"- 工程师：{tech.get('name', '')}（{tech.get('service_region') or '区域待定'}）\n"
                f"- 上门时间：{time_range}\n"
                f"- 温馨提示：如对工程师安排不满意，可直接告诉我调整；取消报修请提前2小时告知。"
                f"{note}\n"
                f"工程师出发前会电话联系您，请保持手机畅通。\n")

    def create_engineer_recommendation_message(self, original_tech: Dict[str, Any],
                                               recommended_tech: Dict[str, Any],
                                               appointment_history: Dict[str, Any],
                                               llm=None) -> str:
        """创建工程师替代推荐消息，使用LLM生成个性化措辞"""
        product_type = appointment_history.get('product_type', '该产品')
        start_time = appointment_history.get('start_time', '')

        if llm:
            try:
                prompt = f"""
作为一个专业的家电售后客服，用户报修{product_type}，指定工程师{original_tech['name']}上门，但{original_tech['name']}工程师在{start_time}这个时间段没有档期。

我找到了一位相似的工程师：
- 姓名：{recommended_tech['name']}
- 服务区域：{recommended_tech.get('service_region', '')}
- 专长：{recommended_tech.get('skills', '')}

原工程师专长：{original_tech.get('skills', '')}

请帮我生成一段简洁专业的推荐话术，告诉用户原指定工程师该时间段没空，但替代工程师同样擅长处理该故障且该时段可上门，询问用户是否愿意换由替代工程师上门。

要求：
1. 语气专业、友善
2. 突出替代工程师对该品类的专业能力
3. 明确询问用户意愿
4. 字数控制在100字以内
"""
                response = llm.invoke(prompt)
                if hasattr(response, 'content'):
                    generated_msg = response.content.strip()
                    if generated_msg:
                        return f"\n机器人：{generated_msg}\n"

            except Exception as e:
                print(f"LLM生成推荐消息失败: {e}")

        # 如果LLM失败，使用默认消息
        return (f"\n机器人：抱歉，{original_tech['name']}工程师在{start_time}这个时间段没有档期。"
                f"不过{recommended_tech['name']}工程师同样擅长{product_type}维修且该时段可上门，"
                f"请问是否为您安排{recommended_tech['name']}工程师上门呢？\n")

    def create_recommendation_declined_message(self, llm=None) -> str:
        """创建用户拒绝替代推荐时的消息"""
        if llm:
            try:
                prompt = """
用户拒绝了我推荐的替代上门工程师，请帮我生成一段专业、友好的回复，表达理解并提供其他选择建议。

要求：
1. 表达理解用户的选择
2. 提供其他解决方案（如改约其他时间段、重新指定工程师等）
3. 保持专业和友好的语气
4. 字数控制在60字以内
"""
                response = llm.invoke(prompt)
                if hasattr(response, 'content'):
                    generated_msg = response.content.strip()
                    if generated_msg:
                        return f"\n机器人：{generated_msg}\n"
            except Exception as e:
                print(f"LLM生成拒绝消息失败: {e}")

        # 默认消息
        return "\n机器人：好的，我理解您的选择。您可以改约其他上门时间段，或重新指定工程师，请问您希望怎么安排？\n"

    def create_appointment_failure_message(self, engineer_name: str) -> str:
        """创建报修登记失败消息（无可用工程师档期）"""
        if engineer_name and engineer_name != "未知":
            from services.engineer_service import EngineerService
            engineer_service = EngineerService()
            specific = engineer_service.get_engineer_by_name(engineer_name)
            if specific:
                return f"\n机器人：抱歉，{engineer_name}工程师在您选择的时间段没有档期。请改约其他时间，或由我为您推荐其他擅长该故障的工程师。\n"
            else:
                return f"\n机器人：抱歉，没有找到名为'{engineer_name}'的工程师。请确认工程师姓名，或由我为您推荐合适的工程师。\n"
        return "\n机器人：抱歉，该时间段暂时没有可上门的工程师，请选择其他时间段再试。\n"

    def create_missing_info_questions(self, missing_info: List[str]) -> str:
        """根据缺失信息创建询问"""
        questions = [self.missing_info_prompts.get(field, f"请补充{field}信息") for field in missing_info]
        return "\n" + " ".join(questions) + "\n"

    def create_unrelated_message(self) -> str:
        """创建无关请求的消息"""
        return "[REPLY][报修专员]抱歉，我这边主要负责安居家电的报修和售后安排。请问您需要报修哪种家电？我可以帮您登记上门维修。\n"

    def create_parse_error_message(self) -> str:
        """创建解析错误消息"""
        return "[REPLY][报修专员]\n机器人：抱歉，我刚才没有理解您的意思，请重新描述一下故障和上门需求。\n"

    def create_save_failure_message(self) -> str:
        """创建报修保存失败消息"""
        return "\n机器人：抱歉，报修单保存失败，请稍后重试。\n"
