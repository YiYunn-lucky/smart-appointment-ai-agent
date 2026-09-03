from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from ..base.interfaces import BaseEngineerRepository, BaseScheduleRepository
from ..base.session_manager import SessionManager
from ..models import Engineer, EngineerSchedule


class EngineerRepository(BaseEngineerRepository, BaseScheduleRepository):
    """
    技师数据访问对象
    
    职责：
    1. 技师信息的CRUD操作
    2. 技师排班的管理
    3. 技师可用性检查
    """
    
    def __init__(self, session_manager: SessionManager):
        """
        初始化技师数据仓库
        
        Args:
            session_manager: 会话管理器
        """
        self.session_manager = session_manager

    def add_engineer(self, name: str, gender: Optional[str] = None, strength: Optional[str] = None) -> int:
        """
        添加新技师
        
        Args:
            name: 技师姓名
            gender: 性别
            strength: 专长
            
        Returns:
            新创建的技师ID
        """
        with self.session_manager.session_scope() as session:
            engineer = Engineer(name=name, gender=gender, strength=strength)
            session.add(engineer)
            session.flush()
            return engineer.id

    def get_engineer_by_id(self, engineer_id: int) -> Optional[Dict[str, Any]]:
        """
        根据ID获取技师信息
        
        Args:
            engineer_id: 技师ID
            
        Returns:
            技师信息字典，如果不存在返回None
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
        根据姓名获取技师信息
        
        Args:
            name: 技师姓名
            
        Returns:
            技师信息字典，如果不存在返回None
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
        获取所有技师信息
        
        Returns:
            技师信息列表
        """
        with self.session_manager.session_scope() as session:
            engineers = session.query(Engineer).all()
            return [self._engineer_to_dict(tech) for tech in engineers]

    def get_all_strengths(self) -> List[str]:
        """
        获取所有技师的专长列表
        
        Returns:
            专长列表（去重后）
        """
        with self.session_manager.session_scope() as session:
            strengths = session.query(Engineer.strength).distinct().all()
            return [s[0] for s in strengths if s[0] is not None]

    def update_engineer(self, engineer_id: int, **updates) -> bool:
        """
        更新技师信息
        
        Args:
            engineer_id: 技师ID
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
        删除技师
        
        Args:
            engineer_id: 技师ID
            
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

    # 排班相关方法
    def add_schedule(self, engineer_id: int, start_time: datetime, end_time: datetime, 
                    status: str, appointment_id: Optional[int] = None) -> int:
        """
        添加技师排班
        
        Args:
            engineer_id: 技师ID
            start_time: 开始时间
            end_time: 结束时间
            status: 状态 ('busy' 或 'free')
            appointment_id: 预约ID（如果是忙碌状态）
            
        Returns:
            新创建的排班ID
        """
        with self.session_manager.session_scope() as session:
            schedule = EngineerSchedule(
                engineer_id=engineer_id,
                start_time=start_time,
                end_time=end_time,
                status=status,
                appointment_id=appointment_id
            )
            session.add(schedule)
            session.flush()
            return schedule.id

    def get_engineer_schedules(self, engineer_id: int, date: datetime) -> List[Dict[str, Any]]:
        """
        获取技师指定日期的排班
        
        Args:
            engineer_id: 技师ID
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
        检查技师在指定时间段是否可用
        
        Args:
            engineer_id: 技师ID
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

    def update_schedule_status(self, schedule_id: int, status: str, appointment_id: Optional[int] = None) -> bool:
        """
        更新排班状态
        
        Args:
            schedule_id: 排班ID
            status: 新状态
            appointment_id: 预约ID
            
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
            if appointment_id is not None:
                schedule.appointment_id = appointment_id
                
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

    def get_engineers_by_gender(self, gender: str) -> List[Dict[str, Any]]:
        """
        根据性别获取技师信息
        
        Args:
            gender: 技师性别
            
        Returns:
            技师信息列表
        """
        with self.session_manager.session_scope() as session:
            engineers = session.query(Engineer).filter(
                Engineer.gender == gender
            ).all()
            return [self._engineer_to_dict(tech) for tech in engineers]

    def _engineer_to_dict(self, engineer: Engineer) -> Dict[str, Any]:
        """将技师对象转换为字典"""
        return {
            'id': engineer.id,
            'name': engineer.name,
            'gender': engineer.gender,
            'strength': engineer.strength
        }

    def _schedule_to_dict(self, schedule: EngineerSchedule) -> Dict[str, Any]:
        """将排班对象转换为字典"""
        return {
            'id': schedule.id,
            'engineer_id': schedule.engineer_id,
            'start_time': schedule.start_time,
            'end_time': schedule.end_time,
            'status': schedule.status,
            'appointment_id': schedule.appointment_id
        }
