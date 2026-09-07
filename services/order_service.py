"""
订单/保修服务层

职责：
1. 客户购买订单查询（按手机号）
2. 保修期动态判定：purchase_date + warranty_years 与当前时间比较
"""

import logging
from typing import Dict, Any, List
from datetime import datetime, timedelta
from db.db_router import DatabaseRouter
from config.time_config import TimeConfig

logger = logging.getLogger(__name__)

# 演示用订单种子：覆盖「在保 / 即将出保 / 已超保」三种演示场景
DEMO_ORDERS = [
    # 演示客户 13800138000（王芳）
    {"user_phone": "13800138000", "user_name": "王芳", "product_type": "空调",
     "brand_model": "安居 KFR-35GW", "purchase_date": datetime(2024, 6, 15), "warranty_years": 6},
    {"user_phone": "13800138000", "user_name": "王芳", "product_type": "冰箱",
     "brand_model": "安居 BCD-456W", "purchase_date": datetime(2025, 1, 10), "warranty_years": 3},
    {"user_phone": "13800138000", "user_name": "王芳", "product_type": "洗衣机",
     "brand_model": "安居 XQG80-12", "purchase_date": datetime(2021, 3, 20), "warranty_years": 3},
    # 演示客户 13900000000（李强）
    {"user_phone": "13900000000", "user_name": "李强", "product_type": "热水器",
     "brand_model": "安居 JSQ30-16", "purchase_date": datetime(2025, 8, 2), "warranty_years": 3},
    {"user_phone": "13900000000", "user_name": "李强", "product_type": "烟灶",
     "brand_model": "安居 CXW-260", "purchase_date": datetime(2022, 5, 12), "warranty_years": 3},
]


class OrderService:
    """订单与保修服务类"""

    def __init__(self, db_path: str = 'sqlite:///data/smart_appointment.db'):
        self.db_router = DatabaseRouter(db_path)
        self.order_repo = self.db_router.orders

    def initialize_default_orders(self) -> bool:
        """初始化演示订单（仅当订单表为空时）"""
        try:
            existing = self.order_repo.get_all_orders()
            if existing:
                logger.info(f"订单表中已有 {len(existing)} 条订单，跳过演示种子初始化")
                return True
            for order in DEMO_ORDERS:
                self.order_repo.create_order(**order)
            logger.info(f"演示订单初始化完成，共 {len(DEMO_ORDERS)} 条")
            return True
        except Exception as e:
            logger.error(f"演示订单初始化失败：{e}")
            return False

    def get_orders_by_phone(self, user_phone: str) -> List[Dict[str, Any]]:
        """按手机号查询订单"""
        return self.order_repo.get_orders_by_phone(user_phone)

    def get_warranty_info(self, user_phone: str) -> List[Dict[str, Any]]:
        """按手机号查询订单并计算每条订单的保修状态（in_warranty/截止日）"""
        now = TimeConfig.naive_now()
        orders = self.order_repo.get_orders_by_phone(user_phone)
        result = []
        for order in orders:
            warranty_end = None
            in_warranty = False
            days_left = None
            if order['purchase_date'] and order['warranty_years']:
                warranty_end = order['purchase_date'] + timedelta(days=365 * order['warranty_years'])
                in_warranty = warranty_end > now
                days_left = (warranty_end - now).days if in_warranty else 0
            result.append({
                'id': order['id'],
                'user_phone': order['user_phone'],
                'user_name': order['user_name'],
                'product_type': order['product_type'],
                'brand_model': order['brand_model'],
                'purchase_date': order['purchase_date'],
                'warranty_years': order['warranty_years'],
                'warranty_end': warranty_end,
                'in_warranty': in_warranty,
                'days_left': days_left
            })
        return result
