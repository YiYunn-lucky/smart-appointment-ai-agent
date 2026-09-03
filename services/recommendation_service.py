"""
推荐调度服务

职责：
1. 定时生成售后回访/提醒推荐（保修期到期提醒、维修完成满意度回访）
2. 管理推荐调度任务
3. 提供手动触发推荐功能
"""

import schedule
import time
import threading
from datetime import timedelta
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# 保修期 30 天内到期视为「即将出保」
WARRANTY_EXPIRY_WINDOW_DAYS = 30


class RecommendationService:
    """推荐调度服务类"""

    def __init__(self):
        self.is_running = False
        self.scheduler_thread = None

    def generate_recommendations_job(self) -> Optional[List[Dict[str, Any]]]:
        """定时生成售后提醒任务：
        1. 保修期 30 天内到期的订单 -> 延保/检测提醒
        2. 近 24 小时完工的报修工单 -> 满意度回访
        """
        try:
            from db.db_router import DatabaseRouter
            from config.time_config import TimeConfig
            router = DatabaseRouter()
            now = TimeConfig.naive_now()
            recommendations = []
            logger.info("开始执行定时推荐生成任务...")

            # 1) 保修期临近到期提醒（按手机号去重生成一条汇总提醒）
            orders = router.orders.get_all_orders()
            expiring_phones = {}
            for order in orders:
                if not order['purchase_date'] or not order['warranty_years']:
                    continue
                warranty_end = order['purchase_date'] + timedelta(days=365 * order['warranty_years'])
                days_left = (warranty_end - now).days
                if 0 <= days_left <= WARRANTY_EXPIRY_WINDOW_DAYS:
                    expiring_phones.setdefault(order['user_phone'], []).append(
                        (order['product_type'], warranty_end, days_left)
                    )
            for phone, items in expiring_phones.items():
                desc = "、".join(
                    f"{product_type}（{warranty_end.strftime('%Y-%m-%d')} 到期）" for product_type, warranty_end, _ in items
                )
                rec_id = router.user_behavior.create_recommendation(
                    user_id=phone,
                    recommendation_type='warranty_expiry_reminder',
                    content=f"您名下 {desc} 即将超出保修期，可考虑办理延保或预约一次全面检测保养，回复「延保/保养」即可为您安排。"
                )
                logger.info(f"生成保修到期提醒：客户 {phone}, 推荐ID={rec_id}")
                recommendations.append({'type': 'warranty_expiry_reminder', 'phone': phone})

            # 2) 近 24 小时完工工单 -> 满意度回访
            recent_tickets = [
                t for t in router.tickets.get_tickets(status='completed')
                if t.get('closed_at') and (now - t['closed_at']) < timedelta(hours=24)
            ]
            for ticket in recent_tickets:
                rec_id = router.user_behavior.create_recommendation(
                    user_id=ticket['user_phone'],
                    recommendation_type='satisfaction_followup',
                    content=(
                        f"您好，您报修的{ticket['product_type']}故障（工单号 {ticket['ticket_no']}）"
                        f"已于 {ticket['closed_at'].strftime('%Y-%m-%d %H:%M')} 维修完成"
                        f"（工程师：{ticket.get('engineer_name') or '待确认'}）。"
                        f"请问本次维修服务您是否满意？如需帮助可随时联系我们。"
                    ),
                    engineer_id=ticket.get('engineer_id')
                )
                logger.info(f"生成满意度回访：工单 {ticket['ticket_no']}, 推荐ID={rec_id}")
                recommendations.append({'type': 'satisfaction_followup', 'ticket_no': ticket['ticket_no']})

            if recommendations:
                logger.info(f"成功生成 {len(recommendations)} 条售后提醒/回访")
                return recommendations
            logger.info("本次没有生成新的推荐")
            return None

        except Exception as e:
            logger.error(f"定时推荐生成任务失败: {str(e)}")
            return None
    
    def start_scheduler(self) -> bool:
        """启动定时任务调度器"""
        if self.is_running:
            logger.warning("调度器已经在运行中")
            return False
        
        try:
            # 设置定时任务
            # 每天9点、14点、19点检查并生成推荐
            schedule.every().day.at("09:00").do(self.generate_recommendations_job)
            schedule.every().day.at("14:00").do(self.generate_recommendations_job)
            schedule.every().day.at("19:00").do(self.generate_recommendations_job)
            
            # 每2小时检查一次（用于测试，实际可根据需要调整）
            schedule.every(2).hours.do(self.generate_recommendations_job)
            
            self.is_running = True
            
            def run_scheduler():
                logger.info("推荐调度器已启动")
                while self.is_running:
                    schedule.run_pending()
                    time.sleep(60)  # 每分钟检查一次
                logger.info("推荐调度器已停止")
            
            # 在后台线程中运行调度器
            self.scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
            self.scheduler_thread.start()
            return True
            
        except Exception as e:
            logger.error(f"启动推荐调度器失败: {str(e)}")
            return False
        
    def stop_scheduler(self) -> bool:
        """停止定时任务调度器"""
        try:
            self.is_running = False
            schedule.clear()
            logger.info("推荐调度器已停止")
            return True
        except Exception as e:
            logger.error(f"停止推荐调度器失败: {str(e)}")
            return False
    
    def run_immediate_check(self) -> Optional[List[Dict[str, Any]]]:
        """立即执行一次推荐检查（用于测试或手动触发）"""
        logger.info("执行立即推荐检查...")
        return self.generate_recommendations_job()
    
    def get_status(self) -> Dict[str, Any]:
        """获取调度器状态"""
        return {
            "is_running": self.is_running,
            "thread_alive": self.scheduler_thread.is_alive() if self.scheduler_thread else False,
            "next_job": str(schedule.next_run()) if schedule.jobs else None,
            "total_jobs": len(schedule.jobs)
        }

# 测试用的手动运行函数
if __name__ == "__main__":
    print("启动推荐调度器测试...")
    service = RecommendationService()
    service.start_scheduler()
    
    try:
        # 运行10分钟用于测试
        time.sleep(600)
    except KeyboardInterrupt:
        print("收到中断信号，停止调度器...")
    finally:
        service.stop_scheduler()
