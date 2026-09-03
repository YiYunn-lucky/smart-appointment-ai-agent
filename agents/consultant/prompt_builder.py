"""
提示词构建器

负责构建各种类型的提示词
"""

from typing import List, Dict, Any


class PromptBuilder:
    """提示词构建器"""
    
    def __init__(self):
        self.system_prompt = self._create_system_prompt()
        self.classification_prompt_template = self._create_classification_prompt_template()
    
    def _create_system_prompt(self) -> str:
        """创建系统提示词"""
        return (
            "你是安居家电的售后顾问，负责为客户解答关于家电保修政策、收费标准、故障排查建议、"
            "退换货政策、上门维修预约时间、延保、安装、保养等问题。"
            "我会为你提供相关的知识库信息，请基于这些信息来回答用户的问题。"
            "如果知识库中没有相关信息，请提供合理的兜底回答，比如："
            "- 对于用户设备是否在保等订单问题：请您提供购买时的11位手机号或订单号，我可以为您查询保修状态。"
            "- 对于其他缺失信息：请您拨打售后热线400-820-9000咨询，或提供订单号让我为您进一步查询。"
            "请用专业、礼貌、简洁的语言回复用户。"
            "如果用户的问题与安居家电售后服务完全无关（如天气、股票、新闻等），请礼貌地告知用户你只能回答家电售后相关问题。"
            "回答时要自然流畅，不要明显地表现出是在查阅资料。"
        )

    def _create_classification_prompt_template(self) -> str:
        """创建分类提示词模板"""
        return (
            "你是一个分类器，判断用户输入是否是关于安居家电售后的咨询类问题。\n"
            "咨询类问题包括：保修政策、收费标准、故障排查建议、退换货、安装保养、上门服务时间、延保等。\n"
            "非咨询类问题包括：报修登记（我要报修、预约上门维修等）以及天气、股票、新闻等完全无关的话题。\n"
            "如果是咨询类问题，回答'YES'。如果是报修登记类问题或完全无关问题，回答'NO'。\n"
            "只回答YES或NO。\n\n"
            "用户输入：{user_input}"
        )
    
    def build_consultation_prompt(self, user_input: str, knowledge_docs: List[Dict[str, Any]]) -> str:
        """构建咨询提示词"""
        context = self._build_knowledge_context(knowledge_docs)
        return f"{self.system_prompt}\n\n{context}\n用户问题：{user_input}\n\n请回答用户的问题。"
    
    def build_classification_prompt(self, user_input: str) -> str:
        """构建分类提示词"""
        return self.classification_prompt_template.format(user_input=user_input)
    
    def _build_knowledge_context(self, knowledge_docs: List[Dict[str, Any]]) -> str:
        """构建知识库上下文"""
        if not knowledge_docs:
            return "没有找到直接相关的知识库信息，请基于你对家电售后服务的一般了解回答，并提示用户拨打400-820-9000或提供订单号进一步核实。"

        context = "\n以下是相关的知识库信息：\n"
        for i, doc in enumerate(knowledge_docs, 1):
            context += f"{i}. {doc['content']}\n"
        context += "\n请基于以上信息回答用户问题。如果知识库信息不足以回答问题，请结合安居家电售后政策的一般常识补充回答，并建议用户提供订单号以便进一步查询。\n"

        return context
