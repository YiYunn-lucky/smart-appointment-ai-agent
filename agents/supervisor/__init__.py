"""
主管（客服调度）域：工具注册表与规划决策（M14 工具化）。

supervisor 层不承载业务状态：只登记"子 Agent / 确定性处理方"的工具描述，
供主管选择工具、注入分类提示词与后续权限门禁复用。
"""

from .tool_registry import SupervisorToolRegistry, ToolSpec, DEFAULT_TOOL_DEFS

__all__ = ["SupervisorToolRegistry", "ToolSpec", "DEFAULT_TOOL_DEFS"]
