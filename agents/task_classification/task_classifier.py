"""
任务分类器 - 负责判断用户请求的类型（安居家电售后客服）

职责：
1. 接收用户输入，分析其意图
2. 根据预定义的分类规则，将任务归类为：
   - appointment（报修/上门预约）
   - query（售后咨询与订单/保修/工单进度查询）
   - complaint（投诉/转人工）
   - other（其他无关任务）
3. 提供清晰的分类结果和置信度
"""

from langchain.prompts import PromptTemplate
from langchain_core.language_models.chat_models import BaseChatModel
from typing import Dict, Any


class TaskClassifier:
    """任务分类器 - 使用LLM进行智能任务分类"""

    VALID_CATEGORIES = {'appointment', 'query', 'complaint', 'other'}

    def __init__(self, llm: BaseChatModel, tools_text: str | None = None):
        self.llm = llm
        self.tools_text = tools_text
        self._initialize_prompt()
        self.chain = self.prompt | self.llm

    def _initialize_prompt(self):
        """初始化分类提示词模板"""
        tool_manifest = (f"\n{self.tools_text}\n" if self.tools_text
                         else "\n请将任务归类为以下类别，输出只能选择以下之一：\n"
                              "1. appointment\n2. query\n3. complaint\n4. other\n只返回类别英文名。\n")
        self.prompt = PromptTemplate(
            input_variables=["task"],
            template=(
                "你是安居家电售后智能客服的任务分类助手，你的任务是对本次用户请求进行分类。\n"
                "可用的任务类别：\n"
                "1. appointment（报修登记类）：客户要报修故障家电、预约工程师上门维修或安装，"
                "例如'我家空调不制冷，帮我报修'、'想预约明天下午工程师上门修冰箱'。\n"
                "2. query（咨询查询类）：客户咨询售后政策，或查询订单保修、报修工单进度，"
                "例如'冰箱保修几年？上门维修怎么收费？'、'帮我看下我的订单还在保修期吗'、"
                "'查询一下报修单AX20260903001的进度'。\n"
                "3. complaint（投诉转人工类）：客户表达不满、投诉、要求转人工或找负责人，"
                "例如'我要投诉你们的服务'、'帮我转人工客服'。\n"
                "4. other（其他类）：与安居家电售后服务完全无关的请求，例如问天气、股票、闲聊等。\n"
                + tool_manifest +
                "举例说明：\n"
                "假如task为'空调不制冷，帮我预约个师傅上门看看'，则输出appointment。\n"
                "假如task为'我去年买的冰箱还在保修期内吗'，则输出query。\n"
                "假如task为'报修单AX20260903001现在什么进度了'，则输出query。\n"
                "假如task为'我要投诉维修师傅，给我转人工'，则输出complaint。\n"
                "假如task为'今天天气怎么样'，则输出other。\n"
                "以下是本次归类任务:\n"
                "任务内容：{task}"
            )
        )

    async def classify_task(self, task: str) -> str:
        """
        分类任务

        Args:
            task: 用户输入的任务内容

        Returns:
            str: 分类结果 ('appointment', 'query', 'complaint', 'other')
        """
        try:
            category_msg = await self.chain.ainvoke({"task": task})
            category = category_msg.content.strip().lower()

            # 验证分类结果是否有效
            if category not in self.VALID_CATEGORIES:
                return 'other'  # 默认归类为其他

            return category

        except Exception as e:
            print(f"任务分类失败: {str(e)}")
            return 'other'  # 发生错误时默认归类为其他

    def get_category_description(self, category: str) -> str:
        """获取分类类别的描述信息"""
        descriptions = {
            'appointment': '报修登记 - 家电报修、预约工程师上门维修/安装',
            'query': '咨询查询 - 售后政策咨询、订单保修查询、报修工单进度查询',
            'complaint': '投诉转人工 - 投诉、不满反馈、要求转人工客服',
            'other': '其他任务 - 与安居家电售后服务无关的请求'
        }
        return descriptions.get(category, '未知任务类型')
