from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from ..base.interfaces import BaseEngineerRepository, BaseScheduleRepository
from ..base.session_manager import SessionManager
from ..models import Engineer, EngineerSchedule


class EngineerRepository(BaseEngineerRepository, BaseScheduleRepository):
    """
    工程师数据访问对象

    职责：
    1. 工程师（上门维修人员）信息的CRUD操作
    2. 工程师排班（忙闲档期）的管理
    3. 工程师可用性检查
    """

    def __init__(self, session_manager: SessionManager):
        """
        初始化工程师数据仓库

        Args:
            session_manager: 会话管理器
        """
        self.session_manager = session_manager

    def add_engineer(self, name: str, skills: Optional[str] = None, service_region: Optional[str] = None) -> int:
        """
        添加新工程师

        Args:
            name: 工程师姓名
            skills: 品类专长描述
            service_region: 服务区域

        Returns:
            新创建的工程师ID
        """
        with self.session_manager.session_scope() as session:
            engineer = Engineer(name=name, skills=skills, service_region=service_region)
            session.add(engineer)
            session.flush()
            return engineer.id

    def get_engineer_by_id(self, engineer_id: int) -> Optional[Dict[str, Any]]:
        """
        根据ID获取工程师信息

        Args:
            engineer_id: 工程师ID

        Returns:
            工程师信息字典，如果不存在返回None
        """
        with self.session_manager.session_scope() as session:
            engineer = session.query(Engineer).filter(
                Engineer.id == engineer_id
            ).first()

            if not engineer:
                return None

            return self._engineer_to_dict(engineer)

    def get_engineer_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        根据姓名获取工程师信息

        Args:
            name: 工程师姓名

        Returns:
            工程师信息字典，如果不存在返回None
        """
        with self.session_manager.session_scope() as session:
            engineer = session.query(Engineer).filter(
                Engineer.name == name
            ).first()

            if not engineer:
                return None

            return self._engineer_to_dict(engineer)

    def get_all_engineers(self) -> List[Dict[str, Any]]:
        """
        获取所有工程师信息

        Returns:
            工程师信息列表
        """
        with self.session_manager.session_scope() as session:
            engineers = session.query(Engineer).all()
            return [self._engineer_to_dict(tech) for tech in engineers]

    def get_all_skills(self) -> List[str]:
        """
        获取所有工程师的品类专长文本列表

        Returns:
            专长文本列表（去重后）
        """
        with self.session_manager.session_scope() as session:
            skills = session.query(Engineer.skills).distinct().all()
            return [s[0] for s in skills if s[0] is not None]

    def update_engineer(self, engineer_id: int, **updates) -> bool:
        """
        更新工程师信息

        Args:
            engineer_id: 工程师ID
            **updates: 要更新的字段

        Returns:
            更新是否成功
        """
        with self.session_manager.session_scope() as session:
            engineer = session.query(Engineer).filter(
                Engineer.id == engineer_id
            ).first()

            if not engineer:
                return False

            for key, value in updates.items():
                if hasattr(engineer, key):
                    setattr(engineer, key, value)

            return True

    def delete_engineer(self, engineer_id: int) -> bool:
        """
        删除工程师

        Args:
            engineer_id: 工程师ID

        Returns:
            删除是否成功
        """
        with self.session_manager.session_scope() as session:
            engineer = session.query(Engineer).filter(
                Engineer.id == engineer_id
            ).first()

            if not engineer:
                return False

            session.delete(engineer)
            return True

    def get_engineers_by_region(self, service_region: str) -> List[Dict[str, Any]]:
        """
        按服务区域获取工程师

        Args:
            service_region: 工程师服务区域（模糊匹配）

        Returns:
            工程师信息列表
        """
        with self.session_manager.session_scope() as session:
            engineers = session.query(Engineer).filter(
                Engineer.service_region.contains(service_region)
            ).all()
            return [self._engineer_to_dict(tech) for tech in engineers]

    # 排班相关方法
    def add_schedule(self, engineer_id: int, start_time: datetime, end_time: datetime,
                    status: str, ticket_id: Optional[int] = None) -> int:
        """
        添加工单占用的忙档排班

        Args:
            engineer_id: 工程师ID
            start_time: 开始时间
            end_time: 结束时间
            status: 状态 ('busy' 或 'free')
            ticket_id: 报修工单ID（忙碌状态时写入）

        Returns:
            新创建的排班ID
        """
        with self.session_manager.session_scope() as session:
            schedule = EngineerSchedule(
                engineer_id=engineer_id,
                start_time=start_time,
                end_time=end_time,
                status=status,
                ticket_id=ticket_id
            )
            session.add(schedule)
            session.flush()
            return schedule.id

    def get_engineer_schedules(self, engineer_id: int, date: datetime) -> List[Dict[str, Any]]:
        """
        获取工程师指定日期的排班

        Args:
            engineer_id: 工程师ID
            date: 查询日期

        Returns:
            排班信息列表
        """
        with self.session_manager.session_scope() as session:
            start = datetime(date.year, date.month, date.day)
            end = start + timedelta(days=1)

            schedules = session.query(EngineerSchedule).filter(
                EngineerSchedule.engineer_id == engineer_id,
                EngineerSchedule.start_time >= start,
                EngineerSchedule.end_time < end
            ).all()

            return [self._schedule_to_dict(schedule) for schedule in schedules]

    def is_engineer_available(self, engineer_id: int, start_time: datetime, end_time: datetime) -> bool:
        """
        检查工程师在指定时间段是否可用

        Args:
            engineer_id: 工程师ID
            start_time: 开始时间
            end_time: 结束时间

        Returns:
            是否可用
        """
        with self.session_manager.session_scope() as session:
            conflict = session.query(EngineerSchedule).filter(
                EngineerSchedule.engineer_id == engineer_id,
                EngineerSchedule.status == "busy",
                EngineerSchedule.start_time < end_time,
                EngineerSchedule.end_time > start_time
            ).first()

            return conflict is None

    def update_schedule_status(self, schedule_id: int, status: str, ticket_id: Optional[int] = None) -> bool:
        """
        更新排班状态

        Args:
            schedule_id: 排班ID
            status: 新状态
            ticket_id: 报修工单ID

        Returns:
            更新是否成功
        """
        with self.session_manager.session_scope() as session:
            schedule = session.query(EngineerSchedule).filter(
                EngineerSchedule.id == schedule_id
            ).first()

            if not schedule:
                return False

            schedule.status = status
            if ticket_id is not None:
                schedule.ticket_id = ticket_id

            return True

    def release_schedule_by_ticket(self, ticket_id: int) -> bool:
        """
        根据工单释放对应的忙档排班（取消/关闭工单时释放工程师档期）

        Args:
            ticket_id: 报修工单ID

        Returns:
            是否成功释放
        """
        with self.session_manager.session_scope() as session:
            schedule = session.query(EngineerSchedule).filter(
                EngineerSchedule.ticket_id == ticket_id,
                EngineerSchedule.status == "busy"
            ).first()

            if not schedule:
                return False

            session.delete(schedule)
            return True

    def delete_schedule(self, schedule_id: int) -> bool:
        """
        删除排班

        Args:
            schedule_id: 排班ID

        Returns:
            删除是否成功
        """
        with self.session_manager.session_scope() as session:
            schedule = session.query(EngineerSchedule).filter(
                EngineerSchedule.id == schedule_id
            ).first()

            if not schedule:
                return False

            session.delete(schedule)
            return True

    def _engineer_to_dict(self, engineer: Engineer) -> Dict[str, Any]:
        """将工程师对象转换为字典"""
        return {
            'id': engineer.id,
            'name': engineer.name,
            'skills': engineer.skills,
            'service_region': engineer.service_region
        }

    def _schedule_to_dict(self, schedule: EngineerSchedule) -> Dict[str, Any]:
        """将排班对象转换为字典"""
        return {
            'id': schedule.id,
            'engineer_id': schedule.engineer_id,
            'start_time': schedule.start_time,
            'end_time': schedule.end_time,
            'status': schedule.status,
            'ticket_id': schedule.ticket_id
        }
