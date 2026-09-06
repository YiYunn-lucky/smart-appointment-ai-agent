"""
AutoDream 沉淀策略（纯函数，离线可测）

沉淀口径：
- 资格：某客户「累计 >= 5 次会话 且 首次到最近一次活动跨度 >= 24h」后开始沉淀；
- 增量回放：只回放行为事件ID大于上次 checkpoint 的新事件（幂等，中断可续跑）；
- 置信度：新事件逐条累计到 user_preferences（同值 +1）；
- 冲突降权：某维度本周期只出现单一新值时，同维度其他旧值置信度减半（下限 1），
  持续未再确认的旧偏好自动淡出前排 —— 偏好漂移由数据自然呈现；
- 画像记忆：聚合全量报修事件生成客户画像，写入 user_memories(profile)，
  向量/内容去重，重复沉淀直接跳过（memory_service.upsert_profile_memory）。

时间约定：所有时间为北京时间 naive datetime（与 TimeConfig.naive_now() 对齐）。
行为事件 created_at 存 UTC naive，但仅用于相对跨度计算，时钟一致不影响口径。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from config.time_config import TimeConfig

MIN_SESSIONS = 5
MIN_SPAN_HOURS = 24

# 行为事件里可忽略的占位会话（老路径未传 session_id 时落 default_session）
PLACEHOLDER_SESSION = 'default_session'

# 偏好的四个维度（与 PreferenceManager.PREFERENCE_KEYS 口径一致）
DIMENSION_TYPES = ('engineer_id', 'product_type', 'fault_type', 'time_period')

# 降权衰减因子：未再确认的旧偏好置信度减半
STALE_DECAY = 0.5


def to_naive(value: Any) -> Optional[datetime]:
    """容忍 None / 字符串 / 带时区 datetime，统一为北京 naive datetime"""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if value.tzinfo is not None:
            value = value.astimezone(TimeConfig.BEIJING_TZ).replace(tzinfo=None)
        return value
    except Exception:
        return None


def _session_points(chat_rows: List[Dict[str, Any]],
                    behavior_rows: List[Dict[str, Any]]) -> Tuple[set, int, list]:
    """汇总会话来源：真实会话ID集合、匿名行为的隐式会话数、全部活动时间点"""
    session_ids = {r.get('session_id') for r in chat_rows if r.get('session_id')}
    implicit = 0
    stamps = []
    for row in chat_rows:
        ts = to_naive(row.get('created_at'))
        if ts is not None:
            stamps.append(ts)
    for row in behavior_rows:
        sid = row.get('session_id')
        if sid and sid != PLACEHOLDER_SESSION:
            session_ids.add(sid)
        else:
            implicit += 1  # 无有效会话ID的行为视为一次独立到访
        ts = to_naive(row.get('created_at'))
        if ts is not None:
            stamps.append(ts)
    return session_ids, implicit, stamps


def activity_stats(chat_rows: List[Dict[str, Any]],
                   behavior_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """统计客户活跃度：会话数 + 首次到最近一次活动的跨度（小时）"""
    session_ids, implicit, stamps = _session_points(chat_rows or [], behavior_rows or [])
    span_hours = 0.0
    first_activity = last_activity = None
    if len(stamps) >= 2:
        stamps.sort()
        first_activity, last_activity = stamps[0], stamps[-1]
        span_hours = (last_activity - first_activity).total_seconds() / 3600.0
    return {
        'session_count': len(session_ids) + implicit,
        'span_hours': span_hours,
        'first_activity': first_activity,
        'last_activity': last_activity,
    }


def is_eligible(stats: Dict[str, Any]) -> bool:
    """沉淀资格：累计 >= 5 次会话且首次到最近一次活动跨度 >= 24h"""
    return (stats.get('session_count', 0) >= MIN_SESSIONS
            and stats.get('span_hours', 0.0) >= MIN_SPAN_HOURS)


def replay_events(behavior_rows: List[Dict[str, Any]], since_event_id: int = 0) -> List[Dict[str, Any]]:
    """增量回放：取 ID 大于 checkpoint 的新事件，按时间升序（幂等断点续跑）"""
    rows = [r for r in behavior_rows if r.get('id', 0) > since_event_id]
    rows.sort(key=lambda r: (to_naive(r.get('created_at')) or datetime.min, r.get('id', 0)))
    return rows


def _parse_period(start_time: Any) -> Optional[str]:
    """从上门开始时间解析 上午/下午 时段（9:00-18:00 服务窗口内）"""
    dt = to_naive(start_time)
    if dt is None:
        return None
    return '上午' if dt.hour < 12 else '下午'


# 偏好维度 -> 行为 action_data 里的源字段（fault_type 维度的源是 fault_desc）
DIM_SOURCE_FIELD = {'product_type': 'product_type', 'fault_type': 'fault_desc'}


def _count_repairs(events: List[Dict[str, Any]], dim: str) -> Dict[str, int]:
    """按维度统计报修事件出现次数（键已字符串化）"""
    counts: Dict[str, int] = {}
    for ev in events:
        if ev.get('action_type') != 'repair':
            continue
        data = ev.get('action_data') or {}
        if dim == 'engineer_id':
            raw = ev.get('engineer_id')
            if raw is None:
                raw = data.get('engineer_id')
            value = str(raw) if raw is not None else None
        elif dim == 'time_period':
            value = _parse_period(data.get('start_time'))
        else:
            value = data.get(DIM_SOURCE_FIELD.get(dim, dim))
        if value:
            counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def aggregate_profile(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """聚合客户画像：按维度统计出现次数并排序（值按 次数降序、值升序）"""
    profile: Dict[str, Any] = {
        'repair_count': 0, 'consult_count': 0, 'handover_count': 0, 'other_count': 0,
        'total_events': len(events),
    }
    stamps = []
    for ev in events:
        action = ev.get('action_type')
        if action == 'repair':
            profile['repair_count'] += 1
        elif action == 'consultation':
            profile['consult_count'] += 1
        elif action == 'handover':
            profile['handover_count'] += 1
        else:
            profile['other_count'] += 1
        ts = to_naive(ev.get('created_at'))
        if ts is not None:
            stamps.append(ts)
    profile['first_activity'] = min(stamps) if stamps else None
    profile['last_activity'] = max(stamps) if stamps else None

    repairs = [ev for ev in events if ev.get('action_type') == 'repair']
    for dim in DIMENSION_TYPES:
        counts = _count_repairs(repairs, dim)
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        profile[dim] = ranked
        profile[f'{dim}_total'] = sum(counts.values())
    return profile


def _top_desc(ranked: List[Tuple[str, int]], limit: int = 3) -> str:
    """把 (值, 次数) 排序列表拼成文案片段：空调×3、冰箱×1"""
    parts = [f"{value}×{count}" for value, count in ranked[:limit]]
    return "、".join(parts)


def build_profile_text(profile: Dict[str, Any], user_id: str = "",
                       engineer_names: Optional[Dict[str, str]] = None) -> str:
    """无 LLM 时的确定性画像文案（降级模板；LLM 可用时由 LLM 润色，结构相同）"""
    engineer_names = engineer_names or {}
    dims = []
    if profile.get('engineer_id'):
        ranked = [(engineer_names.get(value, f"#{value}"), count)
                  for value, count in profile['engineer_id']]
        dims.append(f"常用工程师 {_top_desc(ranked)}")
    if profile.get('product_type'):
        dims.append(f"常用品类 {_top_desc(profile['product_type'])}")
    if profile.get('fault_type'):
        dims.append(f"常见故障 {_top_desc(profile['fault_type'])}")
    if profile.get('time_period'):
        dims.append(f"常约时段 {_top_desc(profile['time_period'])}")

    body = "；".join(dims)
    repair, consult = profile.get('repair_count', 0), profile.get('consult_count', 0)
    prefix = f"客户画像（自动沉淀，累计报修{repair}次、咨询{consult}次）"
    if not body:
        return prefix + "：行为数据较少，待更多记录后细化。"
    last = profile.get('last_activity')
    suffix = f"。最近活动：{last:%Y-%m-%d}。" if last else "。"
    return prefix + "：" + body + suffix


def build_profile_prompt(user_id: str, profile: Dict[str, Any],
                         engineer_names: Optional[Dict[str, str]] = None) -> str:
    """构造画像润色提示词（LLM 生成自然语言画像摘要，失败走 build_profile_text 兜底）"""
    engineer_names = engineer_names or {}
    stats_lines = [
        f"- 累计报修 {profile.get('repair_count', 0)} 次、咨询 {profile.get('consult_count', 0)} 次",
        f"- 常用品类：{_top_desc(profile.get('product_type', []), 5) or '暂无'}",
        f"- 常见故障：{_top_desc(profile.get('fault_type', []), 5) or '暂无'}",
        f"- 常用工程师：{_top_desc([(engineer_names.get(v, f'#{v}'), c) for v, c in profile.get('engineer_id', [])], 5) or '暂无'}",
        f"- 常约时段：{_top_desc(profile.get('time_period', []), 5) or '暂无'}",
    ]
    last = profile.get('last_activity')
    if last:
        stats_lines.append(f"- 最近一次活动：{last:%Y-%m-%d}")
    return (
        "你是一名家电售后客户画像分析师。请基于以下统计为老客户生成一段画像摘要，"
        "用于客服二次接待时快速了解客户。\n"
        f"客户手机号：{user_id}\n" + "\n".join(stats_lines) +
        "\n\n要求：\n"
        "1. 一段连贯中文，不超过 100 字，以「客户画像」开头\n"
        "2. 涵盖常用品类、常见故障、常用工程师与常约时段，语气专业中性\n"
        "3. 直接输出摘要文本，不要任何标记或前缀"
    )


def preference_upsert_deltas(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """本次回放事件的偏好增量：{维度: {值: 次数}}（供置信度逐条累计）"""
    deltas: Dict[str, Dict[str, int]] = {dim: {} for dim in DIMENSION_TYPES}
    repairs = [ev for ev in events if ev.get('action_type') == 'repair']
    for dim in DIMENSION_TYPES:
        for value, count in _count_repairs(repairs, dim).items():
            deltas[dim][value] = count
    return deltas


def stale_downweight_rows(deltas: Dict[str, Dict[str, int]],
                          stored_preferences: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """冲突降权候选：维度本周期只出现单一新值，且存有其他旧值行（值不同）——旧值减半

    同一维度新值出现多个（客户在分散使用）时不降权；被再次确认的值不降权。
    返回需要减半的偏好行列表（调用方对行内 id 执行减半）。
    """
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for pref in stored_preferences:
        by_type.setdefault(pref.get('preference_type'), []).append(pref)

    candidates = []
    for dim, new_values in deltas.items():
        stored = by_type.get(dim, [])
        if not stored or len(new_values) != 1:
            continue
        only_value = next(iter(new_values))
        for pref in stored:
            if pref.get('preference_value') != only_value:
                candidates.append(pref)
    return candidates


def format_period(start_time: Any) -> str:
    """兼容入口：返回 上午/下午（无法解析返回 未知）"""
    return _parse_period(start_time) or '未知'
