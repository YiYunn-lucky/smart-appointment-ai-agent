"""
长期记忆召回打分（纯函数，离线可测）

召回分 = 0.6×语义相似度 + 0.3×时效分 + 0.1×重要度分。
Embedding 不可用时语义分为 None，按 (0.3×时效 + 0.1×重要度)/0.4 归一化降级排序。
"""

from typing import Any, Dict, List, Optional

WEIGHT_SEMANTIC = 0.6
WEIGHT_RECENCY = 0.3
WEIGHT_IMPORTANCE = 0.1

RECENCY_WINDOW_DAYS = 30.0


def recency_score(age_days: float) -> float:
    """时效分：30 天内从 1 线性衰减至 0，超出后为 0"""
    return max(0.0, 1.0 - age_days / RECENCY_WINDOW_DAYS)


def recall_score(semantic: Optional[float], age_days: float, importance: float) -> float:
    """组合召回分；语义相似度按 [0,1] 截断"""
    recency = recency_score(age_days)
    if semantic is not None:
        sem = max(0.0, min(1.0, semantic))
        return WEIGHT_SEMANTIC * sem + WEIGHT_RECENCY * recency + WEIGHT_IMPORTANCE * importance
    # 无语义：时效 + 重要度归一化（0.75 / 0.25）
    return (WEIGHT_RECENCY * recency + WEIGHT_IMPORTANCE * importance) / (
        WEIGHT_RECENCY + WEIGHT_IMPORTANCE
    )


def rank_top_k(candidates: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
    """按召回分降序取 Top-k。

    candidates 元素须含 semantic(可 None)/age_days/importance；
    返回元素追加 score 字段（从高到低），空候选返回 []。
    """
    ranked = []
    for cand in candidates:
        score = recall_score(cand.get("semantic"), cand.get("age_days", RECENCY_WINDOW_DAYS),
                             cand.get("importance", 0.5))
        ranked.append({**cand, "score": score})
    ranked.sort(key=lambda c: c["score"], reverse=True)
    return ranked[:top_k] if top_k else ranked
