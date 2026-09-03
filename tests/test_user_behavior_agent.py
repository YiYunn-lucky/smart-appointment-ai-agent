"""
售后用户行为分析离线功能测试（家电版）

覆盖（全部不依赖 LLM key / 真实业务库，基于 temp 库）：
1. 报修行为记录 → 客户偏好分析（偏好工程师/常用产品/故障/时段）
2. 回访时机判定（fresh 记录不提醒、超 30 天提醒）
3. 偏好写入与置信度递增
4. 回访消息家电化文案
"""

from datetime import timedelta

from agents.user_behavior import PatternAnalyzer, BehaviorRecorder, PreferenceManager
from services.user_behavior_service import UserBehaviorService
from config.time_config import TimeConfig


def build_service(temp_db_path) -> UserBehaviorService:
    return UserBehaviorService(db_path=temp_db_path)


def build_components(service) -> tuple:
    return (
        PatternAnalyzer(service),
        BehaviorRecorder(service),
        PreferenceManager(service),
    )


def repair_data(product_type: str = "空调", fault_desc: str = "不制冷",
                start_time: str = "2099-01-01 15:00") -> dict:
    return {"product_type": product_type, "fault_desc": fault_desc,
            "address": "朝阳区望京某小区3号楼", "start_time": start_time}


class TestRepairBehaviorAnalysis:
    """报修行为记录 → 偏好分析"""

    def test_analysis_detects_favorites(self, temp_db_path):
        service = build_service(temp_db_path)
        analyzer, recorder, _ = build_components(service)
        user = "13800138000"

        recorder.record_repair_behavior(repair_data("空调", "不制冷"), engineer_id=2, user_id=user)
        recorder.record_repair_behavior(repair_data("空调", "不制冷"), engineer_id=2, user_id=user)
        recorder.record_repair_behavior(repair_data("冰箱", "不制冷"), engineer_id=3, user_id=user)

        prefs = analyzer.analyze_user_preferences(user)
        assert prefs is not None
        assert prefs["favorite_engineer_id"] == 2
        assert prefs["favorite_product_type"] == "空调"
        assert prefs["favorite_fault_desc"] == "不制冷"
        assert prefs["favorite_time_slot"] == "下午"  # 15:00
        assert prefs["total_repairs"] == 3
        assert prefs["last_repair_date"] is not None

    def test_analysis_no_data_returns_none(self, temp_db_path):
        service = build_service(temp_db_path)
        analyzer, _, _ = build_components(service)
        assert analyzer.analyze_user_preferences("19900000000") is None

    def test_consultation_behaviors_not_counted_as_repairs(self, temp_db_path):
        service = build_service(temp_db_path)
        analyzer, recorder, _ = build_components(service)
        user = "13800138000"
        recorder.record_consultation_behavior({"query": "冰箱怎么保养？"}, user_id=user)
        prefs = analyzer.analyze_user_preferences(user)
        assert prefs is None  # 只有咨询、无报修 → 无偏好可析


class TestReturnReminderTiming:
    """回访提醒时机：距上次报修是否超过阈值"""

    def test_fresh_repair_should_not_remind(self, temp_db_path):
        service = build_service(temp_db_path)
        analyzer, recorder, _ = build_components(service)
        user = "13800138000"
        recorder.record_repair_behavior(repair_data(), engineer_id=1, user_id=user)
        assert analyzer.should_send_return_reminder(user) is False

    def test_old_repair_should_remind(self, temp_db_path):
        service = build_service(temp_db_path)
        analyzer, recorder, _ = build_components(service)
        user = "13800138000"
        recorder.record_repair_behavior(repair_data(), engineer_id=1, user_id=user)

        # 将行为时间拨回 40 天前（模拟历史报修客户）
        from db.models import UserBehavior
        with service.db_router.session_manager.session_scope() as session:
            behavior = session.query(UserBehavior).filter_by(user_id=user).first()
            behavior.created_at = TimeConfig.naive_now() - timedelta(days=40)

        assert analyzer.should_send_return_reminder(user) is True

    def test_no_history_should_not_remind(self, temp_db_path):
        service = build_service(temp_db_path)
        analyzer, _, _ = build_components(service)
        assert analyzer.should_send_return_reminder("19900000000") is False


class TestPreferenceManagement:
    """偏好写入、置信度递增与摘要"""

    def test_update_preferences_from_repair(self, temp_db_path):
        service = build_service(temp_db_path)
        _, _, manager = build_components(service)
        user = "13800138000"

        manager.update_preferences_from_repair(
            user, repair_data("空调", "不制冷", "2099-01-01 09:00"), engineer_id=2
        )

        prefs = manager.get_user_preferences(user)
        types = {p["preference_type"] for p in prefs}
        assert types == {"engineer_id", "time_period", "product_type", "fault_type"}

        summary = manager.get_preference_summary(user)
        assert summary["preferred_engineer_id"] == 2
        assert summary["preferred_product_type"] == "空调"
        assert summary["preferred_time_period"] == "上午"  # 9:00

    def test_confidence_increments_on_repeat(self, temp_db_path):
        service = build_service(temp_db_path)
        _, _, manager = build_components(service)
        user = "13800138000"

        manager.update_product_preference(user, "空调")
        first = next(p for p in manager.get_user_preferences(user)
                     if p["preference_type"] == "product_type")
        assert first["confidence_score"] == 1

        manager.update_product_preference(user, "空调")
        second = next(p for p in manager.get_user_preferences(user)
                      if p["preference_type"] == "product_type")
        assert second["confidence_score"] == 2

        manager.update_product_preference(user, "冰箱")
        third = next(p for p in manager.get_user_preferences(user)
                     if p["preference_type"] == "product_type" and p["preference_value"] == "冰箱")
        assert third["confidence_score"] == 1


class TestReminderMessageCopywriting:
    """回访消息文案家电化"""

    def test_message_with_product_and_engineer_history(self, temp_db_path):
        service = build_service(temp_db_path)
        analyzer, recorder, _ = build_components(service)
        user = "13800138000"
        recorder.record_repair_behavior(repair_data("洗衣机", "脱水异响"), engineer_id=3, user_id=user)

        message = analyzer.generate_return_message(user)
        assert message is not None
        assert "洗衣机" in message
        assert "工程师" in message

    def test_message_without_history_is_generic(self, temp_db_path):
        service = build_service(temp_db_path)
        analyzer, _, _ = build_components(service)
        message = analyzer.generate_return_message("19900000000")
        assert message is not None
        assert "安居家电" in message
