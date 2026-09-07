"""
工程师查找器

负责根据用户报修需求查找合适的上门维修工程师：
1. 指定工程师：查档期 -> 没空则按 skills 相似度推荐替代并请求确认
2. 未指定工程师：按 (品类+故障) 与 skills 相似度排序 -> 逐个查空闲
3. 区域软偏好：地址命中 service_region 的工程师优先，无命中回退全量
"""

from typing import Optional, Dict, Any, Callable, List
from datetime import datetime, timedelta

# 用于从地址中解析城区的关键词（与工程师 service_region 取值对应）
REGION_KEYWORDS = ["海淀", "朝阳", "丰台", "西城", "东城", "石景山", "昌平", "通州", "大兴", "顺义"]


class EngineerFinder:
    """工程师查找器"""

    def __init__(self):
        pass

    def parse_repair_time(self, start_time_str: str, default_minutes: int = 120) -> tuple:
        """解析期望上门时间，返回 (start_time, end_time, minutes)；默认维修时长120分钟"""
        if not start_time_str or start_time_str == "未知":
            return None, None, None
        try:
            from config.time_config import time_config
            start_time = time_config.parse_datetime(start_time_str)
            if start_time is None:
                return None, None, None
            end_time = start_time + timedelta(minutes=default_minutes)
            return start_time, end_time, default_minutes
        except Exception:
            return None, None, None

    def extract_region(self, address: str) -> Optional[str]:
        """从上门地址中提取城区（如'海淀'），用于区域软偏好"""
        if not address:
            return None
        for keyword in REGION_KEYWORDS:
            if keyword in address:
                return keyword
        return None

    def _ranked_engineers(self, query_text: str, engineers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按 (品类+故障) 与工程师 skills 的向量相似度排序；embedding 不可用时保持原顺序"""
        if not engineers:
            return []
        if not query_text:
            return engineers
        try:
            from services.text_embedding import find_best_match_indices
            skills = [e.get('skills') or '' for e in engineers]
            indices = find_best_match_indices(query_text, skills)
            return [engineers[i] for i in indices]
        except Exception:
            return engineers

    def _region_candidates(self, all_engineers: List[Dict[str, Any]], address: str) -> List[Dict[str, Any]]:
        """区域软偏好：地址命中某城区时优先该城区工程师；无命中返回全量"""
        region = self.extract_region(address)
        if not region:
            return all_engineers, None
        matched = [e for e in all_engineers if e.get('service_region') and region in e['service_region']]
        return (matched if matched else all_engineers), region

    def _find_first_available(self, candidates: List[Dict[str, Any]], start_time: datetime,
                              end_time: datetime, engineer_service, yield_func: Optional[Callable]) -> Optional[Dict]:
        """按顺序找出第一个在档期可用的工程师"""
        for engineer in candidates:
            if engineer_service.is_engineer_available(engineer["id"], start_time, end_time):
                if yield_func:
                    yield_func(f"[THOUGHT][报修专员] 找到可上门的工程师：{engineer['name']}\n")
                return engineer
        return None

    def find_specific_engineer(self, engineer_name: str, start_time: datetime,
                               end_time: datetime, yield_func: Optional[Callable] = None) -> Optional[Dict]:
        """查找指定工程师的可用性"""
        from services.engineer_service import EngineerService
        engineer_service = EngineerService()

        if yield_func:
            yield_func(f"[THOUGHT][报修专员] 用户指定了工程师：{engineer_name}，正在查询该工程师档期...\n")

        specific = engineer_service.get_engineer_by_name(engineer_name)
        if specific:
            if yield_func:
                yield_func(f"[THOUGHT][报修专员] 找到工程师：{specific['name']}（{specific.get('service_region') or '区域未知'}），正在检查档期...\n")

            if engineer_service.is_engineer_available(specific["id"], start_time, end_time):
                if yield_func:
                    yield_func(f"[THOUGHT][报修专员] {engineer_name}工程师在指定时间有空\n")
                return specific
            else:
                if yield_func:
                    yield_func(f"[THOUGHT][报修专员] {engineer_name}工程师在指定时间不空闲\n")
                return None
        else:
            if yield_func:
                yield_func(f"[THOUGHT][报修专员] 未找到名为'{engineer_name}'的工程师\n")
            return None

    def find_similar_available_engineer(self, target_engineer: Dict[str, Any], address: str,
                                        start_time: datetime, end_time: datetime,
                                        yield_func: Optional[Callable] = None) -> Optional[Dict]:
        """目标工程师不可用时：按 skills 相似度 + 区域软偏好查找替代工程师"""
        from services.engineer_service import EngineerService
        engineer_service = EngineerService()

        if yield_func:
            yield_func(f"[THOUGHT][报修专员] 正在根据{target_engineer['name']}的专长查找相似工程师...\n")

        all_engineers = engineer_service.get_all_engineers()
        if not all_engineers:
            return None

        other_engineers = [e for e in all_engineers if e['id'] != target_engineer['id']]
        if not other_engineers:
            return None

        query_text = target_engineer.get('skills') or ''
        region_candidates, region = self._region_candidates(other_engineers, address)
        if region and yield_func:
            yield_func(f"[THOUGHT][报修专员] 优先考虑服务{region}的工程师...\n")

        ranked = self._ranked_engineers(query_text, region_candidates)
        candidate = self._find_first_available(ranked, start_time, end_time, engineer_service, yield_func)
        if candidate:
            return candidate

        # 区域候选全无档期时，回退到全量工程师
        if len(region_candidates) < len(other_engineers):
            if yield_func:
                yield_func("[THOUGHT][报修专员] 区域内工程师均无档期，扩大范围查找全部工程师...\n")
            ranked_all = self._ranked_engineers(query_text, other_engineers)
            return self._find_first_available(ranked_all, start_time, end_time, engineer_service, yield_func)

        if yield_func:
            yield_func("[THOUGHT][报修专员] 没有找到相似且有档期的工程师\n")
        return None

    def find_available_engineer(self, all_engineers: List[Dict[str, Any]], address: str, query_text: str,
                                start_time: datetime, end_time: datetime,
                                yield_func: Optional[Callable] = None) -> Optional[Dict]:
        """未指定工程师：按技能匹配度 + 区域软偏好查找可用工程师"""
        from services.engineer_service import EngineerService
        engineer_service = EngineerService()

        if yield_func:
            yield_func("[THOUGHT][报修专员] 正在查找擅长该故障且有档期的工程师...\n")

        if not all_engineers:
            return None

        region_candidates, region = self._region_candidates(all_engineers, address)
        if region and yield_func:
            yield_func(f"[THOUGHT][报修专员] 优先考虑服务{region}的工程师...\n")

        ranked = self._ranked_engineers(query_text, region_candidates)
        candidate = self._find_first_available(ranked, start_time, end_time, engineer_service, yield_func)
        if candidate:
            return candidate

        if len(region_candidates) < len(all_engineers):
            if yield_func:
                yield_func("[THOUGHT][报修专员] 区域内工程师均无档期，扩大范围查找全部工程师...\n")
            ranked_all = self._ranked_engineers(query_text, all_engineers)
            return self._find_first_available(ranked_all, start_time, end_time, engineer_service, yield_func)

        if yield_func:
            yield_func("[THOUGHT][报修专员] 没有找到可上门的工程师\n")
        return None

    def find_engineer_with_thought(self, appointment_history: Dict[str, Any],
                                   yield_func: Optional[Callable] = None) -> Optional[Dict]:
        """带思考提示的工程师检索主流程"""
        from services.engineer_service import EngineerService
        engineer_service = EngineerService()

        product_type = appointment_history.get("product_type")
        fault_desc = appointment_history.get("fault_desc")
        address = appointment_history.get("address")
        start_time_str = appointment_history.get("start_time")
        engineer_name = appointment_history.get("engineer_name")

        start_time, end_time, _ = self.parse_repair_time(start_time_str)
        if not start_time or not end_time:
            if yield_func:
                yield_func("[THOUGHT][报修专员] 上门时间信息不完整，无法匹配工程师\n")
            return None

        if yield_func:
            yield_func("[THOUGHT][报修专员] 正在解析上门时间并匹配工程师...\n")

        query_text = f"{product_type or ''} {fault_desc or ''}".strip()

        # 优先处理指定工程师
        if engineer_name and engineer_name != "未知":
            specific = self.find_specific_engineer(engineer_name, start_time, end_time, yield_func)
            if specific:
                return specific

            target = engineer_service.get_engineer_by_name(engineer_name)
            if target:
                similar = self.find_similar_available_engineer(target, address, start_time, end_time, yield_func)
                if similar:
                    return {
                        'is_recommendation': True,
                        'original_engineer': target,
                        'recommended_engineer': similar,
                        'requires_confirmation': True
                    }
            return None

        # 通用匹配：全部工程师按技能相似度排序
        all_engineers = engineer_service.get_all_engineers()
        if not all_engineers:
            if yield_func:
                yield_func("[THOUGHT][报修专员] 没有找到任何工程师数据\n")
            return None

        return self.find_available_engineer(all_engineers, address, query_text, start_time, end_time, yield_func)
