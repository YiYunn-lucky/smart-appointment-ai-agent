"""
M13 AutoDream 沉淀：仓储与服务集成测试（全离线，不触网不触 LLM）

覆盖：资格判定（<5 会话跳过）、完整沉淀（偏好置信度/画像记忆/checkpoint）、
增量幂等（无新事件不重复累计）、冲突降权（偏好漂移旧值减半）、
画像记忆轮换（至多一条活跃 profile）、LLM 通道与模板兜底、
任务锁（持锁拒绝/崩溃残留超时接管）、批量扫描。
"""

from datetime import datetime, timedelta
import pytest

from config.time_config import TimeConfig
from db.base.session_manager import SessionManager
from db.db_router import DatabaseRouter
from db.models import UserBehavior
from db.repositories.dream_checkpoint_repository import DreamCheckpointRepository
from services.dream_service import DreamService
from services.memory_service import MemoryService

PHONE = '13800138000'


@pytest.fixture(autouse=True)
def offline_embed(monkeypatch):
    """离线测试：禁止真实 embedding 调用，画像记忆走无语义路径"""
    def raise_embed(_text):
        raise RuntimeError("离线测试：禁止调用 embedding 服务")

    monkeypatch.setattr("services.text_embedding.embed_input", raise_embed)


@pytest.fixture
def db_path(tmp_path):
    return f"sqlite:///{tmp_path / 'dream.db'}"


@pytest.fixture
def engineer_id(db_path) -> int:
    router = DatabaseRouter(db_path)
    return router.engineers.add_engineer('张建国', '空调/冰箱', '海淀区')


def _now():
    return TimeConfig.naive_now()


def seed_behavior(db_path: str, user_id: str, engineer_id: int, created_at: datetime,
                  session_id: str, product_type: str = '空调', fault_desc: str = '不制冷',
                  start_time: str = '10:00', action_type: str = 'repair') -> int:
    """直插行为事件（显式回拨 created_at，模拟跨 24h 的老客户）"""
    start_dt = datetime.strptime(start_time, '%H:%M')
    data = {
        'product_type': product_type,
        'fault_desc': fault_desc,
        'start_time': created_at.replace(hour=start_dt.hour, minute=start_dt.minute,
                                         second=0, microsecond=0).strftime('%Y-%m-%d %H:%M'),
    }
    with SessionManager(db_path).session_scope() as session:
        row = UserBehavior(user_id=user_id, action_type=action_type, action_data=data,
                           engineer_id=engineer_id, session_id=session_id,
                           created_at=created_at)
        session.add(row)
        session.flush()
        return row.id


def seed_eligible_user(db_path: str, engineer_id: int, n: int = 5,
                       product_type: str = '空调', fault_desc: str = '不制冷') -> int:
    """播种达标客户：n 个会话事件，从 48h 前到 4h 前（跨度 >=24h）"""
    now = _now()
    last_id = None
    for i in range(n):
        created = now - timedelta(hours=48) + timedelta(hours=11 * i)
        last_id = seed_behavior(
            db_path, PHONE, engineer_id, created, f"dream-s{n}-{i}",
            product_type=product_type, fault_desc=fault_desc,
            start_time='10:00')
    return last_id


def pref_lookup(db_path: str, user_id: str = PHONE) -> dict:
    """{(偏好类型, 值): 置信度}"""
    return {(p['preference_type'], p['preference_value']): p['confidence_score']
            for p in prefs(db_path)}


def get_svc(db_path: str, llm: bool = False) -> DreamService:
    svc = DreamService(db_path=db_path)
    if not llm:
        svc.profile_text_llm = None  # 离线：关闭 LLM 润色通道，走模板兜底
    return svc


def prefs(db_path: str, user_id: str = PHONE):
    return DatabaseRouter(db_path).user_behavior.get_user_preferences(user_id)


def profile_memories(db_path: str, user_id: str = PHONE):
    return [m for m in MemoryService(db_path).list_memories(user_id)
            if m.get('memory_type') == 'profile']


class TestEligibilityGate:
    def test_ineligible_user_skipped_without_checkpoint(self, db_path, engineer_id):
        now = _now()
        seed_behavior(db_path, PHONE, engineer_id, now - timedelta(hours=3),
                      'a1', start_time='10:00')
        seed_behavior(db_path, PHONE, engineer_id, now - timedelta(hours=2),
                      'a2', start_time='10:00')
        svc = get_svc(db_path)
        result = svc.consolidate_user(PHONE)
        assert result['status'] == 'skipped_not_eligible'
        assert result['session_count'] == 2
        assert result['span_hours'] < 24
        assert svc.checkpoint_repo.get_checkpoint(PHONE) is None
        assert prefs(db_path) == []

    def test_five_sessions_but_short_span_not_eligible(self, db_path, engineer_id):
        now = _now()
        for i in range(5):
            seed_behavior(db_path, PHONE, engineer_id, now - timedelta(minutes=5 * i),
                          f'b{i}', start_time='10:00')
        result = get_svc(db_path).consolidate_user(PHONE)
        assert result['status'] == 'skipped_not_eligible'


class TestConsolidation:
    def test_full_consolidation_writes_preferences_profile_checkpoint(self, db_path, engineer_id):
        seed_eligible_user(db_path, engineer_id)
        svc = get_svc(db_path)
        result = svc.consolidate_user(PHONE)

        assert result['status'] == 'consolidated'
        assert result['events_replayed'] == 5
        assert result['profile_memory'] in ('new', 'superseded')

        # 置信度逐条累计（5 次报修同工程师/同品类/同时段）
        lookup = pref_lookup(db_path)
        assert lookup[('engineer_id', str(engineer_id))] == 5
        assert lookup[('product_type', '空调')] == 5
        assert lookup[('time_period', '上午')] == 5

        # 画像记忆：profile 类型、重要度 0.7、至多一条
        mems = profile_memories(db_path)
        assert len(mems) == 1
        assert mems[0]['importance'] == 0.7
        assert '累计报修5次' in mems[0]['content']

        # checkpoint 幂等推进
        cp = svc.checkpoint_repo.get_checkpoint(PHONE)
        assert cp['run_count'] == 1
        assert cp['total_events_processed'] == 5
        assert cp['is_running'] is False
        assert cp['last_status'] == 'ok'
        assert cp['last_event_id'] == max(
            b['id'] for b in DatabaseRouter(db_path).user_behavior.get_user_behaviors(PHONE))

    def test_second_run_no_new_events_no_double_count(self, db_path, engineer_id):
        seed_eligible_user(db_path, engineer_id)
        svc = get_svc(db_path)
        svc.consolidate_user(PHONE)
        before = prefs(db_path)
        mems_before = profile_memories(db_path)

        result = svc.consolidate_user(PHONE)
        assert result['status'] == 'no_new_events'
        after = prefs(db_path)
        assert {p['id']: p['confidence_score'] for p in before} == \
            {p['id']: p['confidence_score'] for p in after}
        assert len(profile_memories(db_path)) == len(mems_before) == 1
        cp = svc.checkpoint_repo.get_checkpoint(PHONE)
        assert cp['run_count'] == 1  # 空跑不累计运行次数

    def test_incremental_replay_only_new_events(self, db_path, engineer_id):
        seed_eligible_user(db_path, engineer_id)
        svc = get_svc(db_path)
        svc.consolidate_user(PHONE)

        new_id = seed_behavior(db_path, PHONE, engineer_id, _now() - timedelta(minutes=30),
                               'new-session', product_type='空调',
                               fault_desc='漏水', start_time='10:00')
        result = svc.consolidate_user(PHONE)
        assert result['status'] == 'consolidated'
        assert result['events_replayed'] == 1

        lookup = pref_lookup(db_path)
        assert lookup[('engineer_id', str(engineer_id))] == 6
        assert lookup[('product_type', '空调')] == 6
        assert lookup[('fault_type', '漏水')] == 1  # 新故障独立成行
        cp = svc.checkpoint_repo.get_checkpoint(PHONE)
        assert cp['last_event_id'] == new_id
        assert cp['run_count'] == 2

    def test_force_bypasses_eligibility(self, db_path, engineer_id):
        now = _now()
        seed_behavior(db_path, PHONE, engineer_id, now - timedelta(hours=1), 'f1')
        svc = get_svc(db_path)
        assert svc.consolidate_user(PHONE)['status'] == 'skipped_not_eligible'
        result = svc.consolidate_user(PHONE, force=True)
        assert result['status'] == 'consolidated'
        assert result['events_replayed'] == 1


class TestConflictDownweight:
    def test_drift_halves_stale_preferences(self, db_path, engineer_id):
        seed_eligible_user(db_path, engineer_id, product_type='空调', fault_desc='不制冷')
        svc = get_svc(db_path)
        svc.consolidate_user(PHONE)

        # 偏好漂移：新阶段全换成 冰箱/异响 + 工程师2
        router = DatabaseRouter(db_path)
        eng2 = router.engineers.add_engineer('李强', '冰箱/洗衣机', '海淀区')
        now = _now()
        for i in range(3):
            seed_behavior(db_path, PHONE, eng2, now - timedelta(minutes=20 * (3 - i)),
                          f'drift-{i}', product_type='冰箱', fault_desc='异响',
                          start_time='15:00')
        result = svc.consolidate_user(PHONE)
        assert result['status'] == 'consolidated'
        assert result['decayed_count'] == 4  # 工程师/品类/故障/时段四维度旧值各减半

        lookup = pref_lookup(db_path)
        # 旧值 5 -> max(1, 5//2)=2；新值 +3
        assert lookup[('engineer_id', str(engineer_id))] == 2
        assert lookup[('product_type', '空调')] == 2
        assert lookup[('fault_type', '不制冷')] == 2
        assert lookup[('time_period', '上午')] == 2
        # 漂移新值置信度升为 3，排名反超旧值（偏好漂移由数据呈现）
        assert lookup[('engineer_id', str(eng2))] == 3
        assert lookup[('product_type', '冰箱')] == 3
        assert lookup[('fault_type', '异响')] == 3
        assert lookup[('time_period', '下午')] == 3


class TestProfileMemoryChannel:
    def test_llm_text_used_and_supersedes_previous(self, db_path, engineer_id):
        async def fake_llm(user_id, profile, engineer_names):
            return "客户画像（LLM润色版）：常用工程师张建国，常用品类空调。"
        svc = get_svc(db_path, llm=True)
        svc.profile_text_llm = fake_llm
        seed_eligible_user(db_path, engineer_id)
        svc.consolidate_user(PHONE)
        mems = profile_memories(db_path)
        assert len(mems) == 1
        assert mems[0]['content'] == "客户画像（LLM润色版）：常用工程师张建国，常用品类空调。"

        # 二次沉淀：无新事件不产生重复画像记忆
        assert svc.consolidate_user(PHONE)['status'] == 'no_new_events'
        assert len(profile_memories(db_path)) == 1

    def test_llm_failure_falls_back_to_template(self, db_path, engineer_id):
        async def broken_llm(user_id, profile, engineer_names):
            raise RuntimeError("模拟 LLM 不可用")
        svc = get_svc(db_path, llm=True)
        svc.profile_text_llm = broken_llm
        seed_eligible_user(db_path, engineer_id)
        svc.consolidate_user(PHONE)
        content = profile_memories(db_path)[0]['content']
        assert '累计报修5次' in content  # 模板兜底


class TestTaskLock:
    def test_locked_when_other_runner_holds_lock(self, db_path, engineer_id):
        seed_eligible_user(db_path, engineer_id)
        # 模拟另一实例正在处理：先持锁
        other = DreamService(db_path)
        assert other.checkpoint_repo.try_acquire_lock(PHONE) is True
        result = get_svc(db_path).consolidate_user(PHONE)
        assert result['status'] == 'locked'

    def test_stale_lock_taken_over_after_timeout(self, db_path, engineer_id):
        seed_eligible_user(db_path, engineer_id)
        other = DreamService(db_path)
        assert other.checkpoint_repo.try_acquire_lock(PHONE) is True
        # 模拟崩溃残留：回拨持锁时间超过 30 分钟
        with SessionManager(db_path).session_scope() as session:
            from db.models import DreamCheckpoint
            cp = session.query(DreamCheckpoint).filter(
                DreamCheckpoint.user_id == PHONE).first()
            cp.running_started_at = TimeConfig.naive_now() - timedelta(hours=2)
        result = get_svc(db_path).consolidate_user(PHONE)
        assert result['status'] == 'consolidated'
        cp = other.checkpoint_repo.get_checkpoint(PHONE)
        assert cp['is_running'] is False
        assert cp['run_count'] == 1


class TestScanAndRepo:
    def test_scan_consolidates_eligible_only(self, db_path, engineer_id):
        seed_eligible_user(db_path, engineer_id)
        now = _now()
        seed_behavior(db_path, '13900139000', engineer_id, now - timedelta(hours=2),
                      'x1', product_type='冰箱', start_time='10:00')
        results = get_svc(db_path).run_immediate_check()
        assert [r['user_id'] for r in results] == [PHONE]
        assert results[0]['status'] == 'consolidated'

    def test_checkpoint_unique_per_user_and_release_idempotent(self, db_path, engineer_id):
        seed_eligible_user(db_path, engineer_id)
        repo1 = DreamCheckpointRepository(DatabaseRouter(db_path).session_manager)
        repo2 = DreamCheckpointRepository(DatabaseRouter(db_path).session_manager)
        assert repo1.try_acquire_lock(PHONE) is True
        assert repo2.try_acquire_lock(PHONE) is False  # 持锁期间拒绝
        assert repo1.release_lock(PHONE, processed_events=0) is True
        assert repo2.try_acquire_lock(PHONE) is True
        assert repo2.release_lock(PHONE, processed_events=0) is True
        # 空跑不累计计数
        cp = repo1.get_checkpoint(PHONE)
        assert cp['run_count'] == 0
        checkpoints = repo1.list_checkpoints()
        assert len(checkpoints) == 1
        assert checkpoints[0]['user_id'] == PHONE

    def test_release_advances_checkpoint_only_when_processed(self, db_path):
        repo = DreamCheckpointRepository(DatabaseRouter(db_path).session_manager)
        repo.try_acquire_lock(PHONE)
        repo.release_lock(PHONE, processed_events=3, last_event_id=42, status='ok')
        cp = repo.get_checkpoint(PHONE)
        assert cp['last_event_id'] == 42
        assert cp['total_events_processed'] == 3
        assert cp['run_count'] == 1
        assert cp['is_running'] is False

    def test_get_status_reports_config(self, db_path):
        status = get_svc(db_path).get_status()
        assert status['min_sessions'] == 5
        assert status['min_span_hours'] == 24
        assert isinstance(status['recent_checkpoints'], list)
