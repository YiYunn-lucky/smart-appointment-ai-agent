"""
长期记忆召回打分纯函数测试

覆盖：0.6/0.3/0.1 权重合成、时效 30 天衰减、语义不可用归一化降级、
Top-5 截断与降序、空候选边界。
"""

import pytest

from services.memory_scoring import (
    recency_score,
    recall_score,
    rank_top_k,
    WEIGHT_SEMANTIC,
    WEIGHT_RECENCY,
    WEIGHT_IMPORTANCE,
    RECENCY_WINDOW_DAYS,
)


class TestRecallScore:
    def test_weights_compose_correctly(self):
        # sem=1.0, age=0（时效 1）, importance=1.0 → 0.6 + 0.3 + 0.1 = 1.0
        assert recall_score(1.0, 0.0, 1.0) == pytest.approx(1.0)
        # 仅语义分：0.6
        assert recall_score(1.0, RECENCY_WINDOW_DAYS, 0.0) == pytest.approx(WEIGHT_SEMANTIC)
        # 仅时效分：0.3
        assert recall_score(0.0, 0.0, 0.0) == pytest.approx(WEIGHT_RECENCY)
        # 仅重要度：0.1
        assert recall_score(0.0, RECENCY_WINDOW_DAYS, 1.0) == pytest.approx(WEIGHT_IMPORTANCE)

    def test_semantic_clipped_to_unit_interval(self):
        # 余弦越界（负值）按 0 计
        score = recall_score(-0.5, RECENCY_WINDOW_DAYS, 0.0)
        assert score == pytest.approx(0.0)
        score = recall_score(1.5, RECENCY_WINDOW_DAYS, 0.0)
        assert score == pytest.approx(WEIGHT_SEMANTIC)

    def test_recency_linear_decay_within_30_days(self):
        assert recency_score(0.0) == pytest.approx(1.0)
        assert recency_score(RECENCY_WINDOW_DAYS / 2) == pytest.approx(0.5)
        assert recency_score(RECENCY_WINDOW_DAYS) == pytest.approx(0.0)
        assert recency_score(60.0) == pytest.approx(0.0)

    def test_embedding_unavailable_falls_back_normalized(self):
        # sem=None 时按 0.75(时效)/0.25(重要度) 归一
        assert recall_score(None, 0.0, 1.0) == pytest.approx(1.0)
        assert recall_score(None, RECENCY_WINDOW_DAYS, 0.0) == pytest.approx(0.0)
        # age=15 天、imp=0.5： (0.3*0.5 + 0.1*0.5)/0.4 = 0.5
        assert recall_score(None, RECENCY_WINDOW_DAYS / 2, 0.5) == pytest.approx(0.5)


class TestRankTopK:
    def test_ranks_by_score_desc_and_truncates(self):
        candidates = [
            {"content": "新而相关", "semantic": 1.0, "age_days": 0.0, "importance": 0.5},
            {"content": "旧但重要", "semantic": 0.1, "age_days": 5.0, "importance": 1.0},
            {"content": "最旧不相关", "semantic": 0.0, "age_days": 60.0, "importance": 0.0},
        ]
        result = rank_top_k(candidates, top_k=2)
        assert len(result) == 2
        assert result[0]["content"] == "新而相关"
        assert all("score" in r for r in result)
        assert result[0]["score"] >= result[1]["score"]

    def test_embedding_unavailable_candidates_ranked_by_recency_importance(self):
        candidates = [
            {"content": "最新", "semantic": None, "age_days": 0.0, "importance": 0.1},
            {"content": "较旧但重要", "semantic": None, "age_days": 10.0, "importance": 1.0},
        ]
        result = rank_top_k(candidates, top_k=5)
        # 0.75*1 + 0.25*0.1 > 0.75*(2/3) + 0.25*1 → "最新" 排前
        assert result[0]["content"] == "最新"
        assert len(result) == 2

    def test_empty_candidates(self):
        assert rank_top_k([]) == []
        assert rank_top_k([], top_k=5) == []
