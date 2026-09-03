"""
工程师API

提供工程师信息和排班查询接口
"""

from fastapi import APIRouter, HTTPException
from typing import List
from pydantic import BaseModel

router = APIRouter(prefix="/api/engineers", tags=["工程师管理"])


class EngineerResponse(BaseModel):
    """工程师信息响应"""
    id: int
    name: str
    skills: str = ""
    service_region: str = ""


class ScheduleResponse(BaseModel):
    """排班信息响应"""
    id: int
    engineer_id: int
    start_time: str
    end_time: str
    status: str
    ticket_id: int | None = None


@router.get("/", response_model=List[EngineerResponse], summary="获取所有工程师")
async def get_all_engineers():
    """获取所有工程师信息"""
    try:
        from services.engineer_service import EngineerService
        engineer_service = EngineerService()
        engineer_service.initialize_default_engineers()
        engineers = engineer_service.get_all_engineers()

        return [
            EngineerResponse(
                id=tech["id"],
                name=tech["name"],
                skills=tech.get("skills", ""),
                service_region=tech.get("service_region", "")
            )
            for tech in engineers
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取工程师信息失败: {str(e)}")


@router.get("/schedules/today", summary="获取所有工程师今日排班")
async def get_all_engineers_schedule_today():
    """获取所有工程师今天的排班信息"""
    try:
        from services.engineer_service import EngineerService
        from config.time_config import time_config

        engineer_service = EngineerService()
        engineer_service.initialize_default_engineers()

        # 获取所有工程师
        all_engineers = engineer_service.get_all_engineers()
        today = time_config.today()

        schedules_data = []
        for tech in all_engineers:
            tech_id = tech["id"]
            tech_name = tech["name"]

            # 获取该工程师今天的排班
            tech_schedules = engineer_service.get_engineer_schedules(tech_id, today)

            busy_periods = []
            for sched in tech_schedules:
                busy_periods.append({
                    "start": sched["start_time"].strftime("%H:%M") if hasattr(sched["start_time"], 'strftime') else str(sched["start_time"]),
                    "end": sched["end_time"].strftime("%H:%M") if hasattr(sched["end_time"], 'strftime') else str(sched["end_time"]),
                    "ticket_id": sched.get("ticket_id")
                })

            schedules_data.append({
                "engineer_id": tech_id,
                "engineer_name": tech_name,
                "busy_periods": busy_periods
            })

        return schedules_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取排班信息失败: {str(e)}")


@router.get("/{engineer_id}/schedule", response_model=List[ScheduleResponse], summary="获取工程师今日排班")
async def get_engineer_schedule(engineer_id: int):
    """获取指定工程师今天的排班信息"""
    try:
        from services.engineer_service import EngineerService
        from config.time_config import time_config

        engineer_service = EngineerService()
        engineer_service.initialize_default_engineers()

        # 获取工程师信息确认存在
        tech = engineer_service.get_engineer_by_id(engineer_id)
        if not tech:
            raise HTTPException(status_code=404, detail="工程师不存在")

        # 获取今天的排班
        today = time_config.today()
        schedules = engineer_service.get_engineer_schedules(engineer_id, today)

        return [
            ScheduleResponse(
                id=sched["id"],
                engineer_id=sched["engineer_id"],
                start_time=sched["start_time"].strftime("%H:%M"),
                end_time=sched["end_time"].strftime("%H:%M"),
                status=sched["status"],
                ticket_id=sched.get("ticket_id")
            )
            for sched in schedules
        ]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取排班信息失败: {str(e)}")


@router.get("/{engineer_id}", response_model=EngineerResponse, summary="获取单个工程师信息")
async def get_engineer(engineer_id: int):
    """获取指定工程师的详细信息"""
    try:
        from services.engineer_service import EngineerService

        engineer_service = EngineerService()
        engineer_service.initialize_default_engineers()
        tech = engineer_service.get_engineer_by_id(engineer_id)

        if not tech:
            raise HTTPException(status_code=404, detail="工程师不存在")

        return EngineerResponse(
            id=tech["id"],
            name=tech["name"],
            skills=tech.get("skills", ""),
            service_region=tech.get("service_region", "")
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取工程师信息失败: {str(e)}")
