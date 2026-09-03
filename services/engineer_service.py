# services/engineer_service.py

from typing import List, Dict, Any, Optional
from db.db_router import DatabaseRouter
import logging

logger = logging.getLogger(__name__)

class EngineerService:
    """工程师服务类 - 管理上门维修工程师数据与默认初始化"""

    def __init__(self):
        self.db = DatabaseRouter()

        # 默认工程师数据（8人，覆盖6个品类 x 城区，部分品类有冗余以便替代推荐）
        self.default_engineers = [
            {
                "name": "张建国",
                "skills": "空调维修安装、制冷剂加注、空调不制冷不制热故障检修、空调清洗保养、烟灶安装与维修",
                "service_region": "海淀区"
            },
            {
                "name": "李卫东",
                "skills": "冰箱维修、冰箱不制冷故障检修、压缩机与门封条更换、冰柜维修",
                "service_region": "朝阳区"
            },
            {
                "name": "王海涛",
                "skills": "洗衣机维修、滚筒与波轮洗衣机、脱水异响漏水故障检修、洗衣机电控板维修",
                "service_region": "海淀区"
            },
            {
                "name": "刘志强",
                "skills": "燃气热水器与电热水器维修、打不着火与漏水故障检修、热水器安装",
                "service_region": "朝阳区"
            },
            {
                "name": "陈国华",
                "skills": "净水器维修、滤芯更换、净水器漏水不出水故障检修、净水器安装",
                "service_region": "丰台区"
            },
            {
                "name": "赵文斌",
                "skills": "中央空调柜机挂机维修、空调安装移机、空调加氟清洗保养",
                "service_region": "丰台区"
            },
            {
                "name": "孙建军",
                "skills": "油烟机燃气灶维修、排烟不畅故障检修、燃气灶点火故障维修、烟灶安装",
                "service_region": "西城区"
            },
            {
                "name": "周永康",
                "skills": "冰箱洗衣机综合维修、制冷系统检修、电机与排水故障处理、小家电通用维修",
                "service_region": "西城区"
            }
        ]

    def initialize_default_engineers(self) -> bool:
        """初始化默认工程师数据"""
        try:
            existing_engineers = self.db.engineers.get_all_engineers()

            if existing_engineers:
                logger.info(f"数据库中已有 {len(existing_engineers)} 位工程师，跳过初始化")
                return True

            logger.info("数据库中无工程师数据，开始初始化默认工程师")

            for tech_data in self.default_engineers:
                try:
                    tech_id = self.db.engineers.add_engineer(
                        name=tech_data['name'],
                        skills=tech_data['skills'],
                        service_region=tech_data['service_region']
                    )
                    logger.debug(f"添加工程师: {tech_data['name']} (ID: {tech_id})")

                except Exception as e:
                    logger.error(f"添加工程师 {tech_data['name']} 失败: {e}")
                    return False

            final_count = len(self.db.engineers.get_all_engineers())
            logger.info(f"工程师初始化完成，共添加 {final_count} 位工程师")
            return True

        except Exception as e:
            logger.error(f"工程师初始化失败: {e}")
            return False

    def get_all_engineers(self) -> List[Dict[str, Any]]:
        """获取所有工程师信息"""
        return self.db.engineers.get_all_engineers()

    def get_engineer_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """根据姓名获取工程师信息"""
        return self.db.engineers.get_engineer_by_name(name)

    def get_engineer_by_id(self, engineer_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取工程师信息"""
        return self.db.engineers.get_engineer_by_id(engineer_id)

    def get_engineer_schedules(self, engineer_id: int, date) -> List[Dict[str, Any]]:
        """获取工程师指定日期的排班信息"""
        return self.db.engineers.get_engineer_schedules(engineer_id, date)

    def is_engineer_available(self, engineer_id: int, start_time, end_time) -> bool:
        """检查工程师在指定时间段是否可用"""
        return self.db.engineers.is_engineer_available(engineer_id, start_time, end_time)

    def get_engineers_by_region(self, service_region: str) -> List[Dict[str, Any]]:
        """按服务区域获取工程师（区域软偏好）"""
        return self.db.engineers.get_engineers_by_region(service_region)

    def get_all_skills(self) -> List[str]:
        """获取所有工程师的品类专长文本（向量匹配候选）"""
        return self.db.engineers.get_all_skills()

    def add_engineer(self, name: str, skills: str = None, service_region: str = None) -> Optional[int]:
        """添加新工程师"""
        try:
            return self.db.engineers.add_engineer(name, skills, service_region)
        except Exception as e:
            logger.error(f"添加工程师失败：{e}")
            return None

    def get_engineers_count(self) -> int:
        """获取工程师总数"""
        engineers = self.db.engineers.get_all_engineers()
        return len(engineers)
