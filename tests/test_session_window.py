"""
短期记忆窗口纯函数测试

覆盖：60% 滚动水位（10 轮容量 → 6 轮触发）、滚动 chunk、摘要降级截断。
"""

from agents.session.session_window import (
    should_roll,
    roll_oldest,
    fallback_summary,
    WINDOW_CAPACITY_ROUNDS,
    ROLLOVER_WATERMARK,
    ROLL_CHUNK,
    SUMMARY_MARK,
)


def _round(content: str, role: str = "user") -> dict:
    return {"role": role, "content": content}


class TestShouldRoll:
    def test_trigger_at_60_percent_watermark(self):
        trigger = round(WINDOW_CAPACITY_ROUNDS * ROLLOVER_WATERMARK)  # 10*0.6 = 6
        assert not should_roll(0)
        assert not should_roll(trigger - 1)
        assert should_roll(trigger)
        assert should_roll(trigger + 1)

    def test_never_rolls_on_empty(self):
        assert not should_roll(0)


class TestRollOldest:
    def test_rolls_oldest_chunk(self):
        window = [_round(f"u{i}") for i in range(5)]
        rolled, remaining = roll_oldest(window, chunk=2)
        assert [r["content"] for r in rolled] == ["u0", "u1"]
        assert len(remaining) == 3
        assert remaining[0]["content"] == "u2"

    def test_window_smaller_than_chunk_rolls_all(self):
        window = [_round("only")]
        rolled, remaining = roll_oldest(window, chunk=ROLL_CHUNK)
        assert len(rolled) == 1
        assert remaining == []


class TestFallbackSummary:
    def test_joins_user_agent_lines(self):
        rounds = [_round("空调不制冷"), _round("好的，请提供地址", role="assistant")]
        summary = fallback_summary(rounds)
        assert "用户：空调不制冷" in summary
        assert "客服：好的，请提供地址" in summary

    def test_truncates_and_marks(self):
        rounds = [_round("很长的内容" * 100) for _ in range(10)]
        summary = fallback_summary(rounds, max_chars=100)
        assert len(summary) <= 100 + len(SUMMARY_MARK)
        assert summary.endswith(SUMMARY_MARK)

    def test_keeps_previous_summary_prefix(self):
        rounds = [_round("新内容")]
        summary = fallback_summary(rounds, prev_summary="旧摘要：空调已上门维修")
        assert summary.startswith("旧摘要：空调已上门维修")
