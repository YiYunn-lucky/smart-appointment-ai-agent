from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()

class Engineer(Base):
    __tablename__ = 'engineers'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    skills = Column(Text, nullable=True)            # 品类专长描述，供向量相似匹配
    service_region = Column(String, nullable=True)  # 服务区域，如"海淀区/朝阳区"
    schedules = relationship("EngineerSchedule", back_populates="engineer", cascade="all, delete-orphan")

class EngineerSchedule(Base):
    __tablename__ = 'engineer_schedules'
    id = Column(Integer, primary_key=True)
    engineer_id = Column(Integer, ForeignKey('engineers.id'))
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    status = Column(String, nullable=False)  # 'busy' or 'free'
    ticket_id = Column(Integer, nullable=True)  # 关联报修工单ID（busy 时写入）
    engineer = relationship("Engineer", back_populates="schedules")

class RepairTicket(Base):
    __tablename__ = 'repair_tickets'
    id = Column(Integer, primary_key=True)
    ticket_no = Column(String, unique=True)  # AX + YYYYMMDD + 当日序号
    user_name = Column(String, nullable=True)
    user_phone = Column(String, nullable=False)
    product_type = Column(String, nullable=False)  # 空调/冰箱/洗衣机/热水器/净水器/烟灶
    fault_desc = Column(Text, nullable=False)
    address = Column(Text, nullable=False)
    start_time = Column(DateTime, nullable=False)   # 约定上门开始时间
    end_time = Column(DateTime, nullable=False)     # 约定上门结束时间（默认+120分钟）
    status = Column(String, nullable=False, default='pending')  # pending/assigned/in_progress/completed/cancelled
    engineer_id = Column(Integer, ForeignKey('engineers.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)  # completed/cancelled 时间
    engineer = relationship("Engineer")

class Order(Base):
    __tablename__ = 'orders'
    id = Column(Integer, primary_key=True)
    user_phone = Column(String, nullable=False, index=True)
    user_name = Column(String, nullable=True)
    product_type = Column(String, nullable=False)
    brand_model = Column(String, nullable=True)
    purchase_date = Column(DateTime, nullable=True)
    warranty_years = Column(Integer, default=3)
    created_at = Column(DateTime, default=datetime.utcnow)

class HumanHandover(Base):
    __tablename__ = 'human_handovers'
    id = Column(Integer, primary_key=True)
    user_name = Column(String, nullable=True)
    user_phone = Column(String, nullable=True)
    ticket_id = Column(Integer, ForeignKey('repair_tickets.id'), nullable=True)
    issue_summary = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    ticket = relationship("RepairTicket")

class KnowledgeDocument(Base):
    __tablename__ = 'knowledge_documents'
    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    category = Column(String, nullable=False)
    keywords = Column(JSON, nullable=True)  # 存储关键词列表
    embedding = Column(JSON, nullable=True)  # 存储嵌入向量
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Integer, default=1)  # 软删除标记

class UserBehavior(Base):
    __tablename__ = 'user_behaviors'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='guest')  # 手机号标识客户；未识别时使用 'guest'
    action_type = Column(String, nullable=False)  # 'appointment'/'repair', 'consultation', 'handover'
    action_data = Column(JSON, nullable=True)  # 存储行为相关的详细数据
    engineer_id = Column(Integer, ForeignKey('engineers.id'), nullable=True)
    session_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    engineer = relationship("Engineer")

class UserPreference(Base):
    __tablename__ = 'user_preferences'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='guest')
    preference_type = Column(String, nullable=False)  # 'product', 'fault', 'time_slot', 'engineer'
    preference_value = Column(String, nullable=False)
    confidence_score = Column(Integer, default=1)  # 偏好的置信度（出现次数）
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserRecommendation(Base):
    __tablename__ = 'user_recommendations'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='guest')
    recommendation_type = Column(String, nullable=False)  # 'warranty_expiry_reminder', 'satisfaction_followup', 'maintenance_advice'
    content = Column(Text, nullable=False)
    engineer_id = Column(Integer, ForeignKey('engineers.id'), nullable=True)
    is_sent = Column(Integer, default=0)  # 是否已发送
    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)
    engineer = relationship("Engineer")

class ChatSession(Base):
    __tablename__ = 'chat_sessions'
    id = Column(Integer, primary_key=True)
    session_id = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(String, nullable=True, index=True)  # 绑定的客户手机号（可空：未识别）
    state_value = Column(String, nullable=True)  # 状态机快照（None=CLASSIFY）
    appointment_slots = Column(JSON, nullable=True)  # 报修槽位（预约抽取中间态）
    message_window = Column(JSON, nullable=True)  # 短期记忆：最近 N 轮 [{role, content}]
    summary_text = Column(Text, nullable=True)  # 滚动摘要（覆盖被滚出的早期轮次）
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserMemory(Base):
    __tablename__ = 'user_memories'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)  # 客户手机号
    content = Column(Text, nullable=False)  # 记忆文本（可注入 LLM 上下文）
    memory_type = Column(String, nullable=False, default='consult')  # 'repair'/'consult'/'preference'/'profile'
    importance = Column(Float, nullable=True, default=0.5)  # 重要度（0-1）
    embedding = Column(JSON, nullable=True)  # 语义向量（Embedding 可用时写入）
    source_session_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Integer, default=1)  # 软删除标记

class DreamCheckpoint(Base):
    __tablename__ = 'dream_checkpoints'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, unique=True, nullable=False, index=True)  # 客户手机号（一人一行）
    last_event_id = Column(Integer, default=0)  # 已回放的最近行为事件ID（幂等 checkpoint）
    run_count = Column(Integer, default=0)  # 成功沉淀次数
    total_events_processed = Column(Integer, default=0)  # 累计回放事件数
    is_running = Column(Integer, default=0)  # 任务锁：1=处理中
    running_started_at = Column(DateTime, nullable=True)  # 锁开始时间（超时自动接管）
    last_run_at = Column(DateTime, nullable=True)  # 最近一次成功沉淀时间
    last_status = Column(String, nullable=True)  # ok / no_new_events / error
    last_error = Column(Text, nullable=True)  # 最近一次失败原因
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
