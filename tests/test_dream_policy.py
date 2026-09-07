"""
M13 AutoDream 沉淀：策略纯函数测试（全离线，不触网）

覆盖：会话统计/资格边界（>=5 会话且跨度 >=24h）、增量回放幂等过滤、
画像聚合（品类/故障/工程师/时段排名）、偏好增量、冲突降权选择、画像文案。
"""

from datetime import datetime, timedelta

from services.dream_policy import (
    MIN_SESSIONS,
    MIN_SPAN_HOURS,
    activity_stats,
    aggregate_profile,
    build_profile_prompt,
    build_profile_text,
    format_period,
    is_eligible,
    preference_upsert_deltas,
    replay_events,
    stale_downweight_rows,
)

T0 = datetime(2026, 9, 1, 9, 0)


def behavior(uid: int, action: str = 'repair', engineer_id: int = 1,
             created_at: datetime = T0, session_id: str = "s1",
             product_type: str = "空调", fault_desc: str = "不制冷",
             start_time: str = "2026-09-01 14:00", **extra) -> dict:
    data = {'product_type': product_type, 'fault_desc': fault_desc,
            'start_time': start_time}
    data.update(extra)
    return {'id': uid, 'user_id': '13800138000', 'action_type': action,
            'action_data': data, 'engineer_id': engineer_id,
            'session_id': session_id, 'created_at': created_at}


def chat_row(session_id: str, created_at: datetime) -> dict:
    return {'session_id': session_id, 'user_id': '13800138000',
            'created_at': created_at, 'updated_at': created_at}


class TestActivityStats:
    def test_counts_real_and_implicit_sessions(self):
        rows = [behavior(1, session_id='sA'), behavior(2, session_id='sA'),
                behavior(3, session_id='default_session'),
                behavior(4, session_id='default_session')]
        stats = activity_stats([], rows)
        assert stats['session_count'] == 3  # sA 去重 1 + 匿名 2

    def test_chat_rows_join_session_pool(self):
        rows = [behavior(1, session_id='s1', created_at=T0)]
        chats = [chat_row('s1', T0), chat_row('s9', T0 + timedelta(hours=2))]
        stats = activity_stats(chats, rows)
        assert stats['session_count'] == 2

    def test_span_hours_first_to_last(self):
        rows = [behavior(1, created_at=T0),
                behavior(2, created_at=T0 + timedelta(hours=30))]
        stats = activity_stats([], rows)
        assert stats['span_hours'] == 30.0
        assert stats['first_activity'] == T0

    def test_no_activity_span_zero(self):
        stats = activity_stats([], [behavior(1)])
        assert stats['span_hours'] == 0.0
        assert stats['session_count'] == 1


class TestEligibility:
    def test_below_session_threshold(self):
        stats = {'session_count': MIN_SESSIONS - 1, 'span_hours': 48.0}
        assert is_eligible(stats) is False

    def test_below_span_threshold(self):
        stats = {'session_count': MIN_SESSIONS, 'span_hours': MIN_SPAN_HOURS - 0.5}
        assert is_eligible(stats) is False

    def test_meets_both_thresholds(self):
        stats = {'session_count': MIN_SESSIONS, 'span_hours': MIN_SPAN_HOURS}
        assert is_eligible(stats) is True


class TestReplayEvents:
    def test_incremental_filter_and_chronological(self):
        rows = [behavior(5, created_at=T0), behavior(9, created_at=T0 + timedelta(days=1)),
                behavior(7, created_at=T0 + timedelta(hours=1))]
        events = replay_events(rows, since_event_id=5)
        assert [e['id'] for e in events] == [7, 9]

    def test_no_events_after_checkpoint(self):
        assert replay_events([behavior(3)], since_event_id=3) == []

    def test_old_events_never_replayed(self):
        assert replay_events([behavior(1)], since_event_id=5) == []


class TestAggregateProfile:
    def test_action_type_counts(self):
        events = [behavior(1, action='repair'), behavior(2, action='consultation'),
                  behavior(3, action='handover'), behavior(4, action='other')]
        profile = aggregate_profile(events)
        assert profile['repair_count'] == 1
        assert profile['consult_count'] == 1
        assert profile['handover_count'] == 1
        assert profile['other_count'] == 1
        assert profile['total_events'] == 4

    def test_dimension_ranking_desc_with_value_asc(self):
        events = [
            behavior(1, engineer_id=1, product_type='空调', fault_desc='不制冷'),
            behavior(2, engineer_id=2, product_type='冰箱', fault_desc='不制冷'),
            behavior(3, engineer_id=1, product_type='空调', fault_desc='异响'),
        ]
        profile = aggregate_profile(events)
        assert profile['engineer_id'] == [('1', 2), ('2', 1)]
        assert profile['product_type'] == [('空调', 2), ('冰箱', 1)]
        assert profile['fault_type'] == [('不制冷', 2), ('异响', 1)]
        assert profile['time_period'] == [('下午', 3)]

    def test_period_parsing_morning_and_afternoon(self):
        profile = aggregate_profile([
            behavior(1, start_time='2026-09-01 10:30'),
            behavior(2, start_time='2026-09-02 15:00'),
        ])
        assert profile['time_period'] == [('上午', 1), ('下午', 1)]

    def test_invalid_period_excluded(self):
        profile = aggregate_profile([behavior(1, start_time='不是时间')])
        assert profile['time_period'] == []

    def test_engineer_id_falls_back_to_action_data(self):
        events = [{'id': 1, 'action_type': 'repair', 'engineer_id': None,
                   'action_data': {'product_type': '空调', 'fault_desc': 'x',
                                   'start_time': '2026-09-01 10:00',
                                   'engineer_id': 7},
                   'session_id': 's1', 'created_at': T0}]
        profile = aggregate_profile(events)
        assert profile['engineer_id'] == [('7', 1)]

    def test_first_and_last_activity(self):
        events = [behavior(1, created_at=T0),
                  behavior(2, created_at=T0 + timedelta(hours=5))]
        profile = aggregate_profile(events)
        assert profile['first_activity'] == T0
        assert profile['last_activity'] == T0 + timedelta(hours=5)


class TestPreferenceDeltas:
    def test_deltas_from_repair_events_only(self):
        events = [
            behavior(1, engineer_id=1, product_type='空调', fault_desc='不制冷'),
            behavior(2, engineer_id=1, product_type='空调', fault_desc='不制冷'),
            behavior(3, action='consultation', session_id='s2'),
        ]
        deltas = preference_upsert_deltas(events)
        assert deltas['engineer_id'] == {'1': 2}
        assert deltas['product_type'] == {'空调': 2}
        assert deltas['fault_type'] == {'不制冷': 2}
        assert deltas['time_period'] == {'下午': 2}
        assert deltas['engineer_id'] == {'1': 2}

    def test_empty_events_produce_empty_deltas(self):
        deltas = preference_upsert_deltas([])
        assert all(v == {} for v in deltas.values())


class TestStaleDownweight:
    def pref(self, pid, pref_type, value, conf):
        return {'id': pid, 'preference_type': pref_type,
                'preference_value': value, 'confidence_score': conf,
                'last_updated': T0}

    def test_single_new_value_decays_other_values_same_dim(self):
        deltas = {'engineer_id': {'2': 3}, 'product_type': {}, 'fault_type': {},
                  'time_period': {}}
        stored = [self.pref(1, 'engineer_id', '1', 5),
                  self.pref(2, 'engineer_id', '2', 1)]
        candidates = stale_downweight_rows(deltas, stored)
        assert [c['id'] for c in candidates] == [1]

    def test_multi_value_window_skips_decay(self):
        deltas = {'engineer_id': {'1': 2, '2': 1}, 'product_type': {},
                  'fault_type': {}, 'time_period': {}}
        stored = [self.pref(1, 'engineer_id', '1', 5),
                  self.pref(2, 'engineer_id', '2', 1)]
        assert stale_downweight_rows(deltas, stored) == []

    def test_no_stored_rows_skips_decay(self):
        deltas = {'engineer_id': {'2': 3}, 'product_type': {}, 'fault_type': {},
                  'time_period': {}}
        assert stale_downweight_rows(deltas, []) == []

    def test_other_dimensions_unaffected(self):
        deltas = {'engineer_id': {'2': 1}, 'product_type': {}, 'fault_type': {},
                  'time_period': {}}
        stored = [self.pref(1, 'product_type', '洗衣机', 4)]
        assert stale_downweight_rows(deltas, stored) == []


class TestProfileText:
    def test_template_covers_all_dimensions(self):
        events = [behavior(1, engineer_id=1, product_type='空调', fault_desc='不制冷'),
                  behavior(2, engineer_id=1, product_type='空调', fault_desc='不制冷'),
                  behavior(3, engineer_id=2, product_type='冰箱', fault_desc='不制冷')]
        text = build_profile_text(aggregate_profile(events), user_id='13800138000',
                                  engineer_names={'1': '张建国', '2': '李强'})
        assert '累计报修3次' in text
        assert '张建国×2' in text
        assert '空调×2' in text
        assert '不制冷×3' in text
        assert '下午×3' in text

    def test_template_low_data_hint(self):
        text = build_profile_text(aggregate_profile([]), user_id='13800138000')
        assert '数据较少' in text

    def test_prompt_contains_stats_and_constraints(self):
        events = [behavior(1, engineer_id=1, product_type='空调', fault_desc='不制冷')]
        profile = aggregate_profile(events)
        prompt = build_profile_prompt('13800138000', profile, {'1': '张建国'})
        assert '客户画像' in prompt
        assert '空调' in prompt
        assert '100 字' in prompt
        assert '张建国' in prompt


class TestFormatPeriod:
    def test_period_bounds(self):
        assert format_period('2026-09-01 09:00') == '上午'
        assert format_period('2026-09-01 12:00') == '下午'
        assert format_period('2026-09-01 17:59') == '下午'
        assert format_period('乱码') == '未知'
