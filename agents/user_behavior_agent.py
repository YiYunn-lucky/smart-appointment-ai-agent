"""
用户行为分析代理（家电售后版）

核心功能：
1. 记录客户行为（报修、咨询等，按手机号归属）
2. 分析客户偏好（偏好工程师、常用产品、常见故障、上门时段）
3. 判断回访时机（保养建议、保修到期、维修后回访）
4. 生成个性化回访消息（含工程师可约时段）
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from config.model_provider import create_chat_model
from config.time_config import TimeConfig
from .user_behavior import PatternAnalyzer, BehaviorRecorder, PreferenceManager

load_dotenv()


class UserBehaviorAgent:
    """用户行为分析代理（家电售后版）"""

    # 单次上门服务时长（分钟），与报修工单默认时长一致
    SERVICE_MINUTES = 120

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        # 延迟导入Services避免循环依赖
        self._user_behavior_service = None

        # 初始化LLM - 按照其他agent的模式
        self.llm = self._initialize_llm()

        try:
            from services.user_behavior_service import UserBehaviorService
            self.behavior_service = UserBehaviorService()

            # 为保持兼容性，创建适配器版本的组件
            self.pattern_analyzer = PatternAnalyzer(self.behavior_service)
            self.behavior_recorder = BehaviorRecorder(self.behavior_service)
            self.preference_manager = PreferenceManager(self.behavior_service)
        except ImportError:
            # 向后兼容处理
            from db import DatabaseRouter
            self.db = DatabaseRouter()
            self.behavior_service = None
            self.pattern_analyzer = PatternAnalyzer(self.db.user_behavior)
            self.behavior_recorder = BehaviorRecorder(self.db.user_behavior)
            self.preference_manager = PreferenceManager(self.db.user_behavior)

    @property
    def user_behavior_service(self):
        """懒加载用户行为服务"""
        if self._user_behavior_service is None:
            from services.user_behavior_service import UserBehaviorService
            self._user_behavior_service = UserBehaviorService()
        return self._user_behavior_service

    def _initialize_llm(self):
        """初始化通用聊天模型"""
        return create_chat_model(temperature=0.7)

    # ===========================================
    # 行为记录
    # ===========================================

    def record_behavior(self, action_type: str, action_data: Dict[str, Any],
                        engineer_id=None, session_id: str = "default_session",
                        user_id: str = "default_user") -> bool:
        """记录用户行为（user_id一般为客户手机号）"""
        try:
            return self.user_behavior_service.record_behavior(
                user_id=user_id,
                action_type=action_type,
                action_data=action_data,
                engineer_id=engineer_id,
                session_id=session_id
            )
        except Exception as e:
            self.logger.error(f"记录用户行为失败: {str(e)}")
            # 回退到适配器组件
            try:
                return bool(self.behavior_recorder.record_behavior(
                    action_type=action_type,
                    action_data=action_data,
                    engineer_id=engineer_id,
                    session_id=session_id,
                    user_id=user_id
                ))
            except Exception as fallback_error:
                self.logger.error(f"回退方法也失败: {str(fallback_error)}")
                return False

    # ===========================================
    # 用户分析
    # ===========================================

    def get_user_analysis(self, user_id: str = "default_user") -> Optional[Dict[str, Any]]:
        """获取用户分析数据（按手机号分析报修记录）"""
        try:
            preferences = self.pattern_analyzer.analyze_user_preferences(user_id)
            if not preferences:
                return None

            days_since_last = None
            last_repair_date = preferences.get('last_repair_date')
            if last_repair_date:
                last_naive = self.pattern_analyzer.to_naive(last_repair_date)
                if last_naive:
                    days_since_last = (TimeConfig.naive_now() - last_naive).days

            return {
                'favorite_engineer_id': preferences.get('favorite_engineer_id'),
                'favorite_product_type': preferences.get('favorite_product_type'),
                'favorite_fault_desc': preferences.get('favorite_fault_desc'),
                'favorite_time_slot': preferences.get('favorite_time_slot'),
                'total_repairs': preferences.get('total_repairs'),
                'last_repair_date': last_repair_date.isoformat() if isinstance(last_repair_date, datetime) else last_repair_date,
                'days_since_last_repair': days_since_last,
                'should_send_reminder': self.pattern_analyzer.should_send_return_reminder(user_id)
            }
        except Exception as e:
            self.logger.error(f"获取用户分析失败: {str(e)}")
            return None

    def generate_reminder_message(self, user_id: str = "default_user") -> Optional[str]:
        """生成保养/回访提醒消息（降级模板路径）"""
        try:
            return self.pattern_analyzer.generate_return_message(user_id)
        except Exception as e:
            self.logger.error(f"生成提醒消息失败: {str(e)}")
            return None

    def _default_reminder_message(self) -> str:
        """无偏好数据时的默认保养/回访文案"""
        return ("尊敬的客户，您好！感谢您使用安居家电售后服务。"
                "如您的家电需要保养或检修，欢迎随时联系我们，"
                "工程师可在每天9:00-18:00为您上门服务。")

    async def generate_personalized_reminder(self, user_id: str = "default_user",
                                             available_times: list = None) -> Optional[str]:
        """使用LLM生成个性化回访消息（结合客户偏好与工程师可约时段）"""
        try:
            analysis = self.get_user_analysis(user_id)
            if not analysis or not analysis.get('favorite_engineer_id'):
                return self._default_reminder_message()

            # 获取工程师信息（擅长品类）
            tech_info = self._get_engineer_info(analysis['favorite_engineer_id'])
            tech_name = (tech_info or {}).get('name') or '您熟悉的工程师'
            tech_skills = (tech_info or {}).get('skills') or ''

            product = analysis.get('favorite_product_type') or '您的家电'
            fault = analysis.get('favorite_fault_desc') or '日常保养'

            # 格式化可约时间
            times_text = "、".join([t["formatted"] for t in (available_times or [])[:3]]) if available_times else "暂无近期空闲档期"

            # 构建LLM提示
            prompt = f"""请为安居家电售后生成一条客户回访消息。

客户信息：
- 常用上门工程师：{tech_name}
- 工程师擅长品类：{tech_skills}
- 客户常用产品：{product}
- 常见故障/需求：{fault}
- 工程师近期可约时间：{times_text}

要求：
1. 语气亲切自然，体现专业与关怀
2. 可结合客户的使用情况，建议保养或故障复查
3. 如果有可约时间，自然地提及具体时间
4. 最后邀请客户预约上门服务
5. 控制在80字以内
6. 直接输出消息内容，不要任何标记"""

            response = await self.llm.ainvoke([{"role": "user", "content": prompt}])
            return response.content.strip()

        except Exception as e:
            self.logger.error(f"LLM生成个性化回访失败: {type(e).__name__}: {str(e)}")
            # 回退到传统模板方法
            return self.generate_reminder_message(user_id)

    async def get_reminder_with_schedule(self, user_id: str = "default_user") -> Dict[str, Any]:
        """获取包含工程师可约时段的完整回访提醒"""
        try:
            analysis = self.get_user_analysis(user_id)
            if not analysis or not analysis.get('favorite_engineer_id'):
                message = await self.generate_personalized_reminder(user_id, [])
                return {
                    "message": message,
                    "engineer_available_times": []
                }

            tech_id = analysis['favorite_engineer_id']
            available_times = self._query_engineer_available_times(tech_id)

            # 生成个性化消息
            message = await self.generate_personalized_reminder(user_id, available_times)

            return {
                "message": message,
                "engineer_available_times": available_times
            }

        except Exception as e:
            self.logger.error(f"获取提醒信息失败: {type(e).__name__}: {str(e)}")
            return {
                "message": "尊敬的客户，您好！系统暂时无法查询工程师档期，请稍后再试，或拨打售后热线400-820-9000咨询。",
                "engineer_available_times": []
            }

    # ===========================================
    # 内部工具
    # ===========================================

    def _get_engineer_info(self, engineer_id) -> Optional[Dict[str, Any]]:
        """查询工程师信息（姓名、擅长品类、服务区域）"""
        try:
            from db import EngineerDBRouter
            tech_db = EngineerDBRouter()
            return tech_db.get_engineer_by_id(engineer_id)
        except Exception as e:
            self.logger.error(f"获取工程师信息失败: {e}")
            return None

    def _query_engineer_available_times(self, tech_id: int, limit: int = 3) -> list:
        """查询工程师今天/明天服务窗口内（9:00-18:00）的空闲时段"""
        from db import EngineerDBRouter

        start_hour, end_hour = TimeConfig.get_business_hours()
        db = EngineerDBRouter()
        now = TimeConfig.naive_now()

        available_times = []
        day_labels = {0: '今天', 1: '明天'}

        for offset in range(0, 2):
            day = now + timedelta(days=offset)

            if offset == 0:
                # 今天从下一个整点开始（不早于营业开始）
                first_hour = max(now.hour + 1, start_hour)
            else:
                first_hour = start_hour

            for hour in range(first_hour, end_hour):
                check_time = day.replace(hour=hour, minute=0, second=0, microsecond=0)
                slot_end = check_time + timedelta(minutes=self.SERVICE_MINUTES)

                if db.is_engineer_available(tech_id, check_time, slot_end):
                    available_times.append({
                        "date": check_time.strftime("%Y-%m-%d"),
                        "time": f"{hour:02d}:00",
                        "formatted": f"{day_labels[offset]}{hour}:00"
                    })
                    if len(available_times) >= limit:
                        return available_times

        return available_times
