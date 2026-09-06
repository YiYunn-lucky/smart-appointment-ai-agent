"""
EDD 评测基础设施（M16）：离线替身模型 + 按角色装配闸门 + 度量

评测全程不触网、不触真实 LLM：把三个 Agent 模块里的 `create_chat_model`
绑定到本地的角色分发工厂（与生产装配的 main/fast 分工一致，仅替换模型来源）：
- agents/task_classification_agent  → 主管分类（fast 通道）
- agents/appointment_agent          → 生成（main）/ 槽位抽取（fast）两个构造位
- agents/consultant_agent           → RAG 生成（main）/ 咨询相关性判定（fast）

SeqFake 按脚本队列返回内容（耗尽后钉住最后一条，多余调用会以计数暴露），
实现与 ChatOpenAI 等价的接口面：invoke/ainvoke（直调与 LCEL 链）、
stream/astream（单块同步/异步流），按一次逻辑调用统计字符数。
每次调用统计 LLM 调用次数与输入字符 ——「token 成本」为字符数/2 的离线估算代理，
真实计费口径由调用次数预算近似（无 Key 环境可复跑）。

同一 Agent 模块每构建一张 Agent 图都会按固定顺序调用 create_chat_model
（构造位 0..k-1 → 角色 0..k-1）；多会话时按构造位取模归位，保证跨会话分组不变。
"""

import importlib
from typing import Dict, List, Optional, Sequence


# 各模块构造位 → 角色（按 __init__ 内 create_chat_model 的调用顺序）
ROLE_MAP = {
    "agents.task_classification_agent": ["classifier"],
    "agents.appointment_agent": ["appointment_main", "appointment_parser"],
    "agents.consultant_agent": ["consult_gen", "consult_yesno"],
}


def _count_chars(messages) -> int:
    """归一化统计输入字符数：接受消息对象 / dict / 字符串 / LCEL 中间值

    经 `prompt | llm` 链调用时 LCEL 会把已渲染的提示词以 StringPromptValue
    传入（.text 属性）；直调时传入消息列表（.content）或整段提示词字符串。
    """
    if messages is None:
        return 0
    if not isinstance(messages, (list, tuple)):
        messages = [messages]
    chars = 0
    for msg in messages:
        if isinstance(msg, str):
            chars += len(msg)
        elif isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str):
                chars += len(content)
        else:
            content = getattr(msg, "content", None)
            if not isinstance(content, str):
                content = getattr(msg, "text", None)  # StringPromptValue 等 LCEL 中间值
            if isinstance(content, str):
                chars += len(content)
    return chars


class _Message:
    def __init__(self, content: str):
        self.content = content

    def __str__(self):
        return self.content


class SeqFake:
    """脚本替身：按顺序返回 contents，耗尽后钉住最后一条；统计调用与输入长度。

    接口面与 ChatOpenAI 对齐（invoke/ainvoke/stream/astream/__call__），
    以兼容直接调用与 `prompt | llm` 的 LCEL 链（同步/异步两条路径）。
    """

    def __init__(self, counter: "LLMCounter", contents: Optional[Sequence[str]] = None):
        self._counter = counter
        self._contents = list(contents) if contents is not None else [""]
        self._i = 0

    def _note_and_next(self, messages) -> str:
        self._counter.note(_count_chars(messages))
        content = self._contents[min(self._i, len(self._contents) - 1)]
        self._i += 1
        return content

    # ---------- 同步面（LCEL 同步链 / message_builder 直调） ----------

    def invoke(self, messages=None, **kwargs) -> _Message:
        return _Message(self._note_and_next(messages))

    def stream(self, messages=None, **kwargs):
        """同步流：一次逻辑调用整体返回（单块），适配 parse_stream 的 chain.stream"""
        yield _Message(self._note_and_next(messages))

    def __call__(self, *args, **kwargs) -> _Message:
        return self.invoke(*args, **kwargs)

    # ---------- 异步面（分类 / 相关性判定 / RAG 生成直调） ----------

    async def ainvoke(self, messages=None, **kwargs) -> _Message:
        return _Message(self._note_and_next(messages))

    async def astream(self, messages=None, **kwargs):
        yield _Message(self._note_and_next(messages))


class LLMCounter:
    """全进程共享的 LLM 调用度量：调用次数 + 输入字符（token ≈ 字符 / 2）"""

    def __init__(self):
        self.calls = 0
        self.chars = 0

    def note(self, chars: int):
        self.calls += 1
        self.chars += chars

    def snapshot(self) -> Dict[str, int]:
        return {"calls": self.calls, "chars": self.chars}

    def delta(self, before: Dict[str, int]) -> Dict[str, int]:
        return {"calls": self.calls - before["calls"], "chars": self.chars - before["chars"]}


class ModelHub:
    """按场景切换各角色的脚本内容，并把三个 Agent 模块的模型工厂换成替身工厂"""

    def __init__(self):
        self.counter = LLMCounter()
        self.scripts: Dict[str, List[str]] = {
            role: [""] for roles in ROLE_MAP.values() for role in roles
        }
        self._saved = {}
        self._ordinals = {module: 0 for module in ROLE_MAP}

    def install(self):
        """把各模块的 create_chat_model 替换为按构造位分发角色的替身工厂"""
        for module_name, roles in ROLE_MAP.items():
            module = importlib.import_module(module_name)
            self._saved[module_name] = getattr(module, "create_chat_model")
            hub = self

            def factory(*args, _roles=roles, _module=module_name, **kwargs):
                # 同一模块每张 Agent 图按固定顺序构造角色；多会话累计构造位 → 取模归组
                ordinal = hub._ordinals[_module]
                role = _roles[ordinal % len(_roles)]
                hub._ordinals[_module] += 1
                return SeqFake(hub.counter, hub.scripts.get(role))

            setattr(module, "create_chat_model", factory)

    def restore(self):
        for module_name, original in self._saved.items():
            importlib.import_module(module_name)
            setattr(importlib.import_module(module_name), "create_chat_model", original)

    def set_script(self, **role_contents: List[str]):
        """为某角色设置脚本队列（未设置的沿用上一次内容）"""
        for role, contents in role_contents.items():
            assert role in self.scripts, f"未知角色: {role}"
            self.scripts[role] = list(contents)

    def reset_ordinals(self):
        """每场景开始前清零各模块构造位（每个新会话运行时都会重建整张 Agent 图）"""
        for module in self._ordinals:
            self._ordinals[module] = 0

    def reset_counter(self):
        self.counter = LLMCounter()
