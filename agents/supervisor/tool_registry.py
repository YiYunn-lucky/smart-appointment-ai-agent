"""
主管 Agent 工具注册表（M14）

客服调度（主管）在本轮对话中的每个决定都落到一个"工具"上：
分类枚举与工具一一对应——主管用 LLM 做工具选择（返回类别即选中工具），
分类处理器按注册表选择工具并执行对应处理方；子 Agent 处理中发出的"无关"
信号会触发主管重新规划（转回再分类，带轮次上限），构成
观察 → 规划 → 执行 → 再观察 的轻量 ReAct 循环。

工具元数据同时服务后续治理：
- risk_tier：read（只读直通）/ confirm（需客户确认）/ write（写库）——
  权限分级白名单（M15）以此为准；
- model_tier：结构化高频入口走 fast 小模型通道，生成类走 main。
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ToolSpec:
    """子 Agent / 处理方入口的工具描述（主管视角的"可调用能力"）"""

    tool_id: str              # 工具标识（计划日志/审计使用）
    name: str                 # 中文名（注入主管提示词）
    description: str          # 触发场景说明（注入主管提示词）
    category: str             # 对应的分类枚举：appointment/query/complaint/other
    handler: str              # 处理方标识：appointment/consultation/complaint/fallback
    risk_tier: str = "write"  # read=只读直通 / confirm=需客户确认 / write=写库
    model_tier: str = "fast"  # 结构化入口走 fast 通道；生成类入口 main


DEFAULT_TOOL_DEFS = [
    ToolSpec(
        tool_id="repair_booking",
        name="报修登记工具",
        description="客户要报修故障家电或预约工程师上门（如空调不制冷、想预约修冰箱）",
        category="appointment",
        handler="appointment",
        risk_tier="write",
        model_tier="fast",
    ),
    ToolSpec(
        tool_id="aftersale_consult",
        name="售后咨询工具",
        description="客户咨询售后政策，或查询订单保修、工单进度（可先走查库直答再检索知识）",
        category="query",
        handler="consultation",
        risk_tier="read",
        model_tier="fast",
    ),
    ToolSpec(
        tool_id="human_handover",
        name="转人工登记工具",
        description="客户表达不满、投诉、要求转人工或找负责人，登记记录并承诺回电",
        category="complaint",
        handler="complaint",
        risk_tier="write",
        model_tier="fast",
    ),
    ToolSpec(
        tool_id="fallback_reply",
        name="能力清单兜底工具",
        description="与家电售后服务无关或能力范围外的请求（如问天气、闲聊）",
        category="other",
        handler="fallback",
        risk_tier="read",
        model_tier="fast",
    ),
]


class SupervisorToolRegistry:
    """主管工具注册表：登记 / 按类别选择 / 生成提示词清单（无外部依赖，可离线测试）"""

    def __init__(self, tools: Optional[List[ToolSpec]] = None):
        self._tools: Dict[str, ToolSpec] = {}
        for spec in tools if tools is not None else DEFAULT_TOOL_DEFS:
            self._tools[spec.tool_id] = spec

    def register(self, spec: ToolSpec) -> None:
        """注册工具（重复 tool_id 覆盖，供扩展）"""
        self._tools[spec.tool_id] = spec

    def list_tools(self) -> List[ToolSpec]:
        return sorted(self._tools.values(), key=lambda t: t.tool_id)

    def get(self, tool_id: str) -> Optional[ToolSpec]:
        return self._tools.get(tool_id)

    def by_category(self, category: str) -> ToolSpec:
        """分类结果 → 工具（未知/非法类别一律落到兜底工具）"""
        for spec in self._tools.values():
            if spec.category == category:
                return spec
        return self._tools["fallback_reply"]

    def choose(self, category: str) -> str:
        """主管规划：返回本轮回合选中的工具 id（观察 → 规划的记录点）"""
        return self.by_category(category).tool_id

    def manifest_text(self) -> str:
        """工具清单文案：注入主管分类提示词，让"分类即工具选择"对齐工具语义"""
        lines = ["【本主管可调用的工具清单（工具与类别一一对应，选择类别即选择工具）】"]
        for spec in self._tools.values():
            lines.append(f"- {spec.name}（{spec.tool_id}）：{spec.description}")
        lines.append("请只输出类别英文名，不要输出其他内容。")
        return "\n".join(lines)
