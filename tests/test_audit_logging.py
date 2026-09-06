"""
M15 权限与安全治理：审计服务与全链路插桩（全离线）

覆盖：审计落库与过滤查询；幂等键治理（成功占位可重放短路、失败不占位可重试）；
TicketService / HandoverService 写路径审计；主管工具选择审计；风险分级字段一致。
"""

from datetime import datetime, timedelta

import pytest

from agents.supervisor.tool_registry import SupervisorToolRegistry
from agents.task_classification.classification_processor import ClassificationProcessor
from agents.task_classification.state_manager import StateManager
from agents.task_classification.task_classifier import TaskClassifier
from conftest import FakeChatModel
from config.constants import SharedState
from services.audit_service import AuditService
from services.handover_service import HandoverService
from services.permission_policy import RISK_READ, RISK_WRITE
from services.ticket_service import TicketService


class TestAuditService:
    def test_record_and_list(self, temp_db_path):
        audit = AuditService(temp_db_path)
        log_id = audit.record(actor='operator', scene='ticket_management', action='create_ticket',
                              resource_type='repair_ticket', resource_id='AX20260906001',
                              risk_tier='write', detail='测试落库')
        assert log_id is not None
        logs = audit.list_logs()
        assert len(logs) == 1
        row = logs[0]
        assert row['scene'] == 'ticket_management'
        assert row['action'] == 'create_ticket'
        assert row['risk_tier'] == RISK_WRITE
        assert row['result'] == 'ok'
        assert row['resource_id'] == 'AX20260906001'
        assert row['actor'] == 'operator'

    def test_list_filters(self, temp_db_path):
        audit = AuditService(temp_db_path)
        audit.record(actor='a', scene='supervisor', action='tool_select', risk_tier='read')
        audit.record(actor='a', scene='human_handover', action='create_handover', risk_tier='write', result='error')
        assert len(audit.list_logs(scene='supervisor')) == 1
        assert len(audit.list_logs(action='create_handover')) == 1
        assert len(audit.list_logs(risk_tier='write')) == 1
        assert len(audit.list_logs(result='error')) == 1
        assert len(audit.list_logs()) == 2


class TestIdempotencyKey:
    def test_ok_record_occupies_key_and_replay_short_circuits(self, temp_db_path):
        audit = AuditService(temp_db_path)
        first = audit.record(actor='operator', scene='ticket_management', action='create_ticket',
                             resource_type='repair_ticket', risk_tier='write',
                             detail='AX20260906001', idem_key='req-001')
        assert first is not None
        # 同键重复写：唯一索引兜底 → None（服务层判定为 replay）
        second = audit.record(actor='operator', scene='ticket_management', action='create_ticket',
                              resource_type='repair_ticket', risk_tier='write',
                              detail='AX20260906001', idem_key='req-001')
        assert second is None
        # 幂等门：命中成功记录 → 返回缓存（不重复执行业务）
        replay = audit.find_replay('req-001')
        assert replay is not None and replay['result'] == 'ok'
        assert replay['detail'] == 'AX20260906001'

    def test_error_record_does_not_occupy_key_then_retry_ok(self, temp_db_path):
        audit = AuditService(temp_db_path)
        audit.record(actor='operator', scene='ticket_management', action='create_ticket',
                     risk_tier='write', result='error', detail='档期冲突', idem_key='req-002')
        # 失败记录不短路（可重试）
        assert audit.find_replay('req-002') is None
        # 重试成功后同键仍可正常落成功记录（失败未占位）
        ok_id = audit.record(actor='operator', scene='ticket_management', action='create_ticket',
                             resource_type='repair_ticket', risk_tier='write',
                             detail='AX20260906002', idem_key='req-002')
        assert ok_id is not None
        assert audit.find_replay('req-002') is not None
        # 失败记录 detail 中保留了幂等键文本便于追溯
        err_rows = [r for r in audit.list_logs(result='error')]
        assert err_rows and 'req-002' in (err_rows[0].get('detail') or '')

    def test_find_replay_without_key_returns_none(self, temp_db_path):
        audit = AuditService(temp_db_path)
        assert audit.find_replay(None) is None
        assert audit.find_replay('') is None


class TestTicketServiceAudit:
    def test_create_ticket_records_audit(self, temp_db_path):
        audit = AuditService(temp_db_path)
        svc = TicketService(db_path=temp_db_path, audit_logger=audit.record,
                            audit_actor='repair_agent')
        ticket = svc.create_ticket(
            user_phone='13800138000', product_type='空调', fault_desc='不制冷',
            address='海淀区中关村某小区3号楼', start_time=datetime.now() + timedelta(days=1),
        )
        assert ticket is not None
        rows = [r for r in audit.list_logs() if r['action'] == 'create_ticket']
        assert len(rows) == 1
        row = rows[0]
        assert row['actor'] == 'repair_agent'
        assert row['scene'] == 'ticket_management'
        assert row['risk_tier'] == RISK_WRITE
        assert row['result'] == 'ok'
        assert row['resource_type'] == 'repair_ticket'
        assert row['resource_id'] == ticket['ticket_no']

    def test_update_status_records_audit(self, temp_db_path):
        audit = AuditService(temp_db_path)
        svc = TicketService(db_path=temp_db_path, audit_logger=audit.record,
                            audit_actor='repair_agent')
        ticket = svc.create_ticket(
            user_phone='13800138000', product_type='冰箱', fault_desc='结冰',
            address='朝阳区望京某小区', start_time=datetime.now() + timedelta(days=2),
        )
        updated = svc.update_status(ticket['id'], 'cancelled')
        assert updated is not None
        rows = [r for r in audit.list_logs() if r['action'] == 'update_status']
        assert len(rows) == 1
        assert rows[0]['result'] == 'ok'
        assert 'pending -> cancelled' in rows[0]['detail']
        assert rows[0]['resource_id'] == ticket['ticket_no']

    def test_without_logger_no_records(self, temp_db_path):
        """未装配审计回调的老调用方零行为变化"""
        audit = AuditService(temp_db_path)
        svc = TicketService(db_path=temp_db_path)
        ticket = svc.create_ticket(
            user_phone='13800138000', product_type='空调', fault_desc='不制冷',
            address='海淀区中关村某小区3号楼', start_time=datetime.now() + timedelta(days=1),
        )
        assert ticket is not None
        assert audit.list_logs() == []


class TestHandoverServiceAudit:
    def test_create_handover_records_audit(self, temp_db_path):
        audit = AuditService(temp_db_path)
        svc = HandoverService(db_path=temp_db_path, audit_logger=audit.record,
                              audit_actor='complaint_agent')
        record = svc.create_handover(issue_summary='维修后仍漏水，要求人工介入',
                                     user_phone='13800138000')
        assert record is not None
        rows = [r for r in audit.list_logs() if r['action'] == 'create_handover']
        assert len(rows) == 1
        row = rows[0]
        assert row['actor'] == 'complaint_agent'
        assert row['scene'] == 'human_handover'
        assert row['risk_tier'] == RISK_WRITE
        assert row['result'] == 'ok'
        assert row['resource_id'] == str(record['id'])
        assert '维修后仍漏水' in row['detail']


class _FakeRouter:
    """替身路由器：记录主管选中处理方，不触碰真实服务"""

    def __init__(self):
        self.calls = []
        self.appointment_agent = object()
        self.consultant_agent = object()

    async def route_to_appointment(self, task):
        self.calls.append('appointment')
        yield "[REPLY]报修"

    async def route_to_consultation(self, task):
        self.calls.append('consultation')
        yield "[REPLY]咨询"

    async def route_to_complaint(self, task):
        self.calls.append('complaint')
        yield "[REPLY]转人工"

    async def handle_unsupported_task(self, category):
        self.calls.append(f'fallback:{category}')
        yield "[REPLY]兜底"


class TestSupervisorAudit:
    def _processor_with_collector(self, category: str):
        classifier = TaskClassifier(FakeChatModel(content=category))
        collector = []
        processor = ClassificationProcessor(
            classifier, StateManager(SharedState()), _FakeRouter(), None,
            audit_logger=lambda **kw: collector.append(kw))
        return processor, collector

    @pytest.mark.asyncio
    async def test_tool_select_records_audit_with_risk_tier(self):
        registry = SupervisorToolRegistry()
        expected = [('appointment', 'repair_booking', RISK_WRITE),
                    ('query', 'aftersale_consult', RISK_READ),
                    ('complaint', 'human_handover', RISK_WRITE),
                    ('other', 'fallback_reply', RISK_READ)]
        for category, tool_id, risk in expected:
            processor, collector = self._processor_with_collector(category)
            chunks = [t async for t in processor.process_task_stream('测试输入')]
            assert chunks, category
            assert len(collector) == 1, category
            row = collector[0]
            assert row['actor'] == 'supervisor'
            assert row['scene'] == 'supervisor'
            assert row['action'] == 'tool_select'
            assert row['tool_id'] == tool_id
            assert row['risk_tier'] == risk
            assert row['result'] == 'ok'
            assert row['resource_type'] == 'tool'
            assert registry.by_category(category).tool_id == tool_id

    @pytest.mark.asyncio
    async def test_unknown_category_falls_back_and_audits(self):
        processor, collector = self._processor_with_collector('pay')  # 废弃类别
        [t async for t in processor.process_task_stream('查账单')]
        assert len(collector) == 1
        assert collector[0]['tool_id'] == 'fallback_reply'
        assert collector[0]['risk_tier'] == RISK_READ

    def test_no_logger_keeps_old_behavior(self):
        """未装配审计回调的主管规划零行为变化（plan_log 仍然记录）"""
        classifier = TaskClassifier(FakeChatModel(content='complaint'))
        processor = ClassificationProcessor(
            classifier, StateManager(SharedState()), _FakeRouter(), None)
        tool = processor.select_tool('complaint')
        assert tool.tool_id == 'human_handover'
        assert processor.plan_log[-1]['tool_id'] == 'human_handover'
