"""
短期记忆窗口（纯函数，离线可测）

窗口容量 10 轮；占用超过 60%（6 轮）时，每轮结束后把最旧 ROLL_CHUNK 轮
滚入滚动摘要，窗口保留最近约 6 轮原文 + 摘要覆盖更早内容。
"""

import re
from typing import Dict, List, Tuple

WINDOW_CAPACITY_ROUNDS = 10
ROLLOVER_WATERMARK = 0.6
ROLL_CHUNK = 2
SUMMARY_MARK = "（早期对话截断）"
FALLBACK_MAX_CHARS = 400

_TOKEN_PATTERN = re.compile(r"\[(THOUGHT|REPLY|ERROR)[^\]]*\]")


def extract_reply_text(stream_text: str) -> str:
    """从令牌流文本提取「对客户可见」的回复：REPLY/ERROR 段正文拼接，跳过 THOUGHT/SIGNAL。

    与前端 index.html 的分段语义一致（段 = 前缀令牌后至下一个令牌之间的文本）。
    """
    if not stream_text:
        return ""
    parts = []
    matches = list(_TOKEN_PATTERN.finditer(stream_text))
    for index, match in enumerate(matches):
        if match.group(1) in ("REPLY", "ERROR"):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(stream_text)
            text = stream_text[match.end():end].strip()
            if text:
                parts.append(text)
    return "\n".join(parts)

_ROLL_TRIGGER = max(1, round(WINDOW_CAPACITY_ROUNDS * ROLLOVER_WATERMARK))


def should_roll(n_rounds: int) -> bool:
    """窗口占用是否达到 60% 水位（append 新轮前对旧内容判定，滚动后保留近约 6 轮）"""
    return n_rounds >= _ROLL_TRIGGER


def roll_oldest(window: List[Dict[str, str]], chunk: int = ROLL_CHUNK) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """从窗口滚出最旧 chunk 轮，返回 (rolled_away, remaining)"""
    if len(window) <= chunk:
        return window, []
    return window[:chunk], window[chunk:]


def fallback_summary(rounds: List[Dict[str, str]], prev_summary: str = "",
                     max_chars: int = FALLBACK_MAX_CHARS) -> str:
    """摘要降级：拼接历史为「用户/客服」文本并截断（无 Key / LLM 失败路径）"""
    lines = [f"用户：{r['content']}" if r.get('role') == 'user' else f"客服：{r['content']}"
             for r in rounds]
    combined = (prev_summary + "\n" + "\n".join(lines)).strip() if prev_summary else "\n".join(lines)
    if len(combined) > max_chars:
        combined = combined[:max_chars]
    return combined + SUMMARY_MARK


def build_summary_prompt(prev_summary: str, rolled_messages: List[Dict[str, str]]) -> str:
    """滚动摘要 prompt：将旧摘要与滚出的对话压缩为新摘要（保持事实要点）"""
    lines = [f"用户：{r['content']}" if r.get('role') == 'user' else f"客服：{r['content']}"
             for r in rolled_messages]
    body = "\n".join(lines)
    prev = prev_summary or "（无）"
    return (
        "你是客服对话摘要器。请把「已有摘要」与「新增对话」压缩为一段不超过 200 字的摘要，"
        "保留：客户身份与手机号、报修品类与故障、上门时间与地址、工单号、投诉诉求、已承诺事项。\n"
        f"已有摘要：{prev}\n新增对话：\n{body}\n\n只输出合并后的摘要正文，不要前缀。"
    )
