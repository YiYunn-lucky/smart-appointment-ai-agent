from typing import List, Dict, Any, Optional
from datetime import datetime
from ..base.interfaces import BaseOrderRepository
from ..base.session_manager import SessionManager
from ..models import Order


class OrderRepository(BaseOrderRepository):
    """
    订单数据访问对象

    职责：
    1. 客户购买订单的记录与查询
    2. 保修期在 service 层动态计算（purchase_date + warranty_years）
    """

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    def create_order(self, user_phone: str, product_type: str, user_name: Optional[str] = None,
                     brand_model: Optional[str] = None, purchase_date: Optional[datetime] = None,
                     warranty_years: int = 3) -> int:
        with self.session_manager.session_scope() as session:
            order = Order(
                user_phone=user_phone,
                user_name=user_name,
                product_type=product_type,
                brand_model=brand_model,
                purchase_date=purchase_date,
                warranty_years=warranty_years
            )
            session.add(order)
            session.flush()
            return order.id

    def get_orders_by_phone(self, user_phone: str) -> List[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            orders = session.query(Order).filter(
                Order.user_phone == user_phone
            ).order_by(Order.purchase_date.desc()).all()
            return [self._order_to_dict(o) for o in orders]

    def get_all_orders(self) -> List[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            orders = session.query(Order).all()
            return [self._order_to_dict(o) for o in orders]

    def _order_to_dict(self, order: Order) -> Dict[str, Any]:
        return {
            'id': order.id,
            'user_phone': order.user_phone,
            'user_name': order.user_name,
            'product_type': order.product_type,
            'brand_model': order.brand_model,
            'purchase_date': order.purchase_date,
            'warranty_years': order.warranty_years,
            'created_at': order.created_at
        }
