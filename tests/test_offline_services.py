"""
服务层离线功能测试（temp 库，不依赖 demo 数据 / LLM key）

覆盖：
1. 报修工单：全生命周期合法流转 / 非法流转拒绝 / 取消释放忙档后可重派
2. 派单档期冲突判定与换人
3. 保修期计算边界（购买 364 天 vs 366 天 × 1 年保修）
4. 演示订单种子（空库初始化 + 幂等跳过）
5. 转人工记录
6. 工程师种子数据（8 人覆盖 6 品类，无 gender/strength 字段）
"""

import re
from datetime import timedelta

from db.db_router import DatabaseRouter
from services.ticket_service import TicketService
from services.order_service import OrderService
from services.handover_service import HandoverService
from services.engineer_service import EngineerService
from config.time_config import TimeConfig

FUTURE_TIME = TimeConfig.naive_now().replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=365)


def seed_engineers(db_path: str, names=("张建国", "李卫东")) -> dict:
    """向 temp 库写入工程师，返回 {name: id}"""
    router = DatabaseRouter(db_path)
    ids = {}
    for index, name in enumerate(names):
        region = ("海淀区", "朝阳区")[index % 2]
        ids[name] = router.engineers.add_engineer(
            name=name, skills="空调冰箱洗衣机维修、故障检修", service_region=region
        )
    return ids


def _future_window(days_offset: int = 365, hour: int = 15):
    """取与 FUTURE_TIME 不冲突的另一窗口（错开天/小时）"""
    return TimeConfig.naive_now().replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days_offset)


class TestTicketLifecycle:
    """工单状态机与派单"""

    def _svc(self, temp_db_path) -> tuple:
        svc = TicketService(db_path=temp_db_path)
        engineers = seed_engineers(temp_db_path)
        return svc, engineers

    def test_create_ticket_generates_ax_ticket_no(self, temp_db_path):
        svc, _ = self._svc(temp_db_path)
        start = _future_window()
        ticket = svc.create_ticket(
            user_phone="13800138000", product_type="空调", fault_desc="不制冷",
            address="朝阳区某小区3号楼", start_time=start
        )
        assert ticket is not None
        assert ticket["status"] == "pending"
        assert re.fullmatch(r"AX\d{10}", ticket["ticket_no"])
        assert ticket["end_time"] is not None  # 默认补齐 120 分钟

    def test_full_lifecycle_transitions(self, temp_db_path):
        svc, engineers = self._svc(temp_db_path)
        ticket = svc.create_ticket(
            user_phone="13800138000", product_type="空调", fault_desc="不制冷",
            address="朝阳区某小区3号楼", start_time=_future_window()
        )
        tid = ticket["id"]

        assigned = svc.assign_ticket(tid, engineers["张建国"])
        assert assigned is not None and assigned["status"] == "assigned"
        assert assigned["engineer_name"] == "张建国"

        started = svc.update_status(tid, "in_progress")
        assert started["status"] == "in_progress"

        done = svc.update_status(tid, "completed")
        assert done["status"] == "completed"
        assert done["closed_at"] is not None

    def test_illegal_transitions_rejected(self, temp_db_path):
        svc, engineers = self._svc(temp_db_path)
        ticket = svc.create_ticket(
            user_phone="13800138000", product_type="空调", fault_desc="不制冷",
            address="海淀区某小区", start_time=_future_window()
        )
        tid = ticket["id"]

        # pending 直接 completed：非法
        assert svc.update_status(tid, "completed") is None
        # 非法状态值
        assert svc.update_status(tid, "closed") is None
        # completed 后再流转
        assert svc.assign_ticket(tid, engineers["张建国"]) is not None
        assert svc.update_status(tid, "in_progress") is not None
        assert svc.update_status(tid, "completed") is not None
        assert svc.update_status(tid, "cancelled") is None  # 终态不可再动
        assert svc.assign_ticket(tid, engineers["张建国"]) is None  # 完成态不可派单

    def test_assign_conflict_when_engineer_busy(self, temp_db_path):
        svc, engineers = self._svc(temp_db_path)
        start = _future_window()
        first = svc.create_ticket(
            user_phone="13800138000", product_type="空调", fault_desc="不制冷",
            address="朝阳区某小区", start_time=start
        )
        assert svc.assign_ticket(first["id"], engineers["张建国"]) is not None

        # 同一时段第二单派给同一工程师：档期冲突
        second = svc.create_ticket(
            user_phone="13900000000", product_type="冰箱", fault_desc="不制冷",
            address="朝阳区另一小区", start_time=start
        )
        assert svc.assign_ticket(second["id"], engineers["张建国"]) is None
        # 换工程师可成功
        assert svc.assign_ticket(second["id"], engineers["李卫东"]) is not None

    def test_cancel_releases_schedule_for_reassign(self, temp_db_path):
        svc, engineers = self._svc(temp_db_path)
        start = _future_window()
        ticket = svc.create_ticket(
            user_phone="13800138000", product_type="空调", fault_desc="不制冷",
            address="海淀区某小区", start_time=start
        )
        assert svc.assign_ticket(ticket["id"], engineers["张建国"]) is not None
        assert svc.update_status(ticket["id"], "cancelled") is not None

        # 忙档已释放：同工程师同窗口可重新接新单
        replacement = svc.create_ticket(
            user_phone="13800138000", product_type="空调", fault_desc="不制冷",
            address="海淀区某小区", start_time=start
        )
        assert svc.assign_ticket(replacement["id"], engineers["张建国"]) is not None

    def test_change_engineer_reassigns(self, temp_db_path):
        svc, engineers = self._svc(temp_db_path)
        ticket = svc.create_ticket(
            user_phone="13800138000", product_type="空调", fault_desc="不制冷",
            address="朝阳区某小区", start_time=_future_window()
        )
        assert svc.assign_ticket(ticket["id"], engineers["张建国"]) is not None
        changed = svc.change_engineer(ticket["id"], engineers["李卫东"])
        assert changed is not None and changed["engineer_id"] == engineers["李卫东"]
        assert changed["engineer_name"] == "李卫东"


class TestWarrantyBoundary:
    """保修期计算边界"""

    def test_in_and_out_of_warranty_by_days(self, temp_db_path):
        now = TimeConfig.naive_now()
        router = DatabaseRouter(temp_db_path)
        router.orders.create_order(
            user_phone="13800138000", user_name="边界客户", product_type="空调",
            brand_model="安居 KFR-35GW", purchase_date=now - timedelta(days=364), warranty_years=1
        )
        router.orders.create_order(
            user_phone="13800138000", user_name="边界客户", product_type="洗衣机",
            brand_model="安居 XQG80", purchase_date=now - timedelta(days=366), warranty_years=1
        )

        infos = {o["product_type"]: o for o in OrderService(db_path=temp_db_path).get_warranty_info("13800138000")}
        assert infos["空调"]["in_warranty"] is True
        assert infos["洗衣机"]["in_warranty"] is False
        # 在保订单应给出剩余天数与截止日
        assert infos["空调"]["warranty_end"] is not None
        assert infos["洗衣机"]["warranty_end"] is not None
        assert infos["洗衣机"]["days_left"] == 0

    def test_demo_orders_seed_and_idempotent(self, temp_db_path):
        svc = OrderService(db_path=temp_db_path)
        assert svc.initialize_default_orders() is True
        orders = svc.get_orders_by_phone("13800138000")
        assert len(orders) == 3

        # 再调用不重复插入
        svc.initialize_default_orders()
        assert len(OrderService(db_path=temp_db_path).get_orders_by_phone("13800138000")) == 3

        # 演示数据状态固定：洗衣机 2021 年购、3 年保 → 已超保
        infos = {o["product_type"]: o for o in svc.get_warranty_info("13800138000")}
        assert infos["洗衣机"]["in_warranty"] is False
        assert infos["空调"]["in_warranty"] is True  # 2024-06 购、6 年保
        assert infos["冰箱"]["in_warranty"] is True  # 2025-01 购、3 年保


class TestEngineerSeedData:
    """工程师种子数据（纯数据校验，不依赖真实库内容）"""

    def test_default_engineers_cover_six_categories(self):
        engineers = EngineerService().default_engineers
        assert len(engineers) == 8
        required_keys = {"name", "skills", "service_region"}
        all_skills = ""
        for eng in engineers:
            assert required_keys.issubset(eng.keys()), eng
            assert "gender" not in eng and "strength" not in eng, eng
            assert eng["name"] and eng["skills"] and eng["service_region"]
            all_skills += eng["skills"]

        for category in ["空调", "冰箱", "洗衣机", "热水器", "净水器", "烟灶"]:
            assert category in all_skills, f"工程师技能语料未覆盖品类：{category}"

    def test_seed_data_writes_into_temp_db(self, temp_db_path):
        """default_engineers 数据可经 repository 成功写入（种子脚本可用性）"""
        router = DatabaseRouter(temp_db_path)
        for eng in EngineerService().default_engineers:
            router.engineers.add_engineer(
                name=eng["name"], skills=eng["skills"], service_region=eng["service_region"]
            )
        stored = router.engineers.get_all_engineers()
        assert len(stored) == 8
        assert "gender" not in stored[0] and "strength" not in stored[0]


class TestHandoverService:
    """转人工记录"""

    def test_create_and_list_handover(self, temp_db_path):
        svc = HandoverService(db_path=temp_db_path)
        record = svc.create_handover(
            issue_summary="对维修结果不满意，要求人工介入",
            user_name="王芳", user_phone="13800138000"
        )
        assert record is not None and record["id"] is not None
        records = svc.list_handovers()
        assert len(records) == 1
        assert records[0]["user_phone"] == "13800138000"
        assert "不满" in records[0]["issue_summary"]
