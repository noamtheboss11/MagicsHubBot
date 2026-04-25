from __future__ import annotations

import json
from typing import Iterable
from typing import TYPE_CHECKING, Any

import aiosqlite

from sales_bot.db import Database
from sales_bot.exceptions import NotFoundError, PermissionDeniedError
from sales_bot.models import CheckoutOrderItemRecord, CheckoutOrderRecord, PurchaseRecord, SystemRecord

if TYPE_CHECKING:
    from sales_bot.bot import SalesBot


class PaymentService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create_purchase(self, user_id: int, system_id: int, paypal_link: str) -> PurchaseRecord:
        purchase_id = await self.database.insert(
            "INSERT INTO paypal_purchases (user_id, system_id, paypal_link) VALUES (?, ?, ?)",
            (user_id, system_id, paypal_link),
        )
        return await self.get_purchase(purchase_id)

    async def get_purchase(self, purchase_id: int) -> PurchaseRecord:
        row = await self.database.fetchone(
            "SELECT * FROM paypal_purchases WHERE id = ?",
            (purchase_id,),
        )
        if row is None:
            raise NotFoundError("Purchase not found.")
        return self._map_purchase(row)

    async def complete_purchase(self, bot: "SalesBot", purchase_id: int, payload: dict[str, Any]) -> PurchaseRecord:
        purchase = await self.get_purchase(purchase_id)
        if purchase.status == "completed":
            return purchase

        system = await bot.services.systems.get_system(purchase.system_id)
        user = await bot.fetch_user(purchase.user_id)
        await bot.services.delivery.deliver_system(
            bot,
            user,
            system,
            source=f"paypal:{purchase.id}",
            granted_by=None,
        )

        await self.database.execute(
            """
            UPDATE paypal_purchases
            SET status = 'completed', completed_at = CURRENT_TIMESTAMP, webhook_payload = ?
            WHERE id = ?
            """,
            (json.dumps(payload), purchase_id),
        )
        return await self.get_purchase(purchase_id)

    async def create_checkout_order(
        self,
        *,
        user_id: int,
        payment_method: str,
        items: Iterable[tuple[SystemRecord, str]],
        subtotal_amount: str,
        discount_amount: str,
        total_amount: str,
        currency: str,
        note: str | None,
        discount_code_id: int | None = None,
        discount_code_text: str | None = None,
    ) -> CheckoutOrderRecord:
        normalized_method = payment_method.strip().lower()
        if normalized_method not in {"card", "paypal"}:
            raise PermissionDeniedError("שיטת התשלום שנבחרה לא נתמכת בקופה.")

        item_list = list(items)
        if not item_list:
            raise PermissionDeniedError("אי אפשר לפתוח קופה בלי מערכות בעגלה.")

        order_id = await self.database.insert(
            """
            INSERT INTO website_checkout_orders (
                user_id,
                payment_method,
                discount_code_id,
                discount_code_text,
                subtotal_amount,
                discount_amount,
                total_amount,
                currency,
                note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                normalized_method,
                discount_code_id,
                discount_code_text.strip().upper() if discount_code_text else None,
                subtotal_amount,
                discount_amount,
                total_amount,
                currency.strip().upper(),
                note.strip() if note else None,
            ),
        )
        try:
            await self.database.executemany(
                """
                INSERT INTO website_checkout_order_items (order_id, system_id, system_name, unit_price, line_total)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (order_id, system.id, system.name, unit_price, unit_price)
                    for system, unit_price in item_list
                ],
            )
        except Exception:
            await self.database.execute("DELETE FROM website_checkout_orders WHERE id = ?", (order_id,))
            raise
        return await self.get_checkout_order(order_id)

    async def get_checkout_order(self, order_id: int) -> CheckoutOrderRecord:
        row = await self.database.fetchone(
            "SELECT * FROM website_checkout_orders WHERE id = ?",
            (order_id,),
        )
        if row is None:
            raise NotFoundError("ההזמנה לא נמצאה.")
        return self._map_checkout_order(row)

    async def list_checkout_order_items(self, order_id: int) -> list[CheckoutOrderItemRecord]:
        rows = await self.database.fetchall(
            "SELECT * FROM website_checkout_order_items WHERE order_id = ? ORDER BY system_name ASC",
            (order_id,),
        )
        return [self._map_checkout_item(row) for row in rows]

    async def list_user_checkout_orders(self, user_id: int) -> list[CheckoutOrderRecord]:
        rows = await self.database.fetchall(
            "SELECT * FROM website_checkout_orders WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        return [self._map_checkout_order(row) for row in rows]

    async def list_pending_checkout_orders(self) -> list[CheckoutOrderRecord]:
        rows = await self.database.fetchall(
            "SELECT * FROM website_checkout_orders WHERE status = 'pending' ORDER BY created_at ASC",
        )
        return [self._map_checkout_order(row) for row in rows]

    async def list_checkout_orders(self, *, limit: int = 100) -> list[CheckoutOrderRecord]:
        rows = await self.database.fetchall(
            "SELECT * FROM website_checkout_orders ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._map_checkout_order(row) for row in rows]

    async def complete_checkout_order(self, bot: "SalesBot", order_id: int, reviewer_id: int) -> CheckoutOrderRecord:
        order = await self.get_checkout_order(order_id)
        if order.status == "completed":
            return order
        if order.status != "pending":
            raise PermissionDeniedError("אפשר להשלים רק הזמנות שעדיין ממתינות לאישור.")

        user = bot.get_user(order.user_id) or await bot.fetch_user(order.user_id)
        items = await self.list_checkout_order_items(order.id)
        for item in items:
            if await bot.services.ownership.user_owns_system(order.user_id, item.system_id):
                continue
            system = await bot.services.systems.get_system(item.system_id)
            await bot.services.delivery.deliver_system(
                bot,
                user,
                system,
                source=f"checkout:{order.id}",
                granted_by=reviewer_id,
            )

        await self.database.execute(
            """
            UPDATE website_checkout_orders
            SET status = 'completed', reviewed_at = CURRENT_TIMESTAMP, reviewed_by = ?, completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (reviewer_id, order.id),
        )
        return await self.get_checkout_order(order.id)

    async def cancel_checkout_order(self, order_id: int, reviewer_id: int, reason: str | None) -> CheckoutOrderRecord:
        order = await self.get_checkout_order(order_id)
        if order.status == "cancelled":
            return order
        if order.status != "pending":
            raise PermissionDeniedError("אפשר לבטל רק הזמנות שעדיין ממתינות לטיפול.")

        await self.database.execute(
            """
            UPDATE website_checkout_orders
            SET status = 'cancelled', reviewed_at = CURRENT_TIMESTAMP, reviewed_by = ?, cancelled_at = CURRENT_TIMESTAMP, cancel_reason = ?
            WHERE id = ?
            """,
            (reviewer_id, reason.strip() if reason else None, order.id),
        )
        return await self.get_checkout_order(order.id)

    @staticmethod
    def _map_purchase(row: aiosqlite.Row) -> PurchaseRecord:
        return PurchaseRecord(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            system_id=int(row["system_id"]),
            status=str(row["status"]),
            paypal_link=str(row["paypal_link"]),
            created_at=str(row["created_at"]),
            completed_at=str(row["completed_at"]) if row["completed_at"] else None,
        )

    @staticmethod
    def _map_checkout_order(row: aiosqlite.Row) -> CheckoutOrderRecord:
        return CheckoutOrderRecord(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            payment_method=str(row["payment_method"]),
            status=str(row["status"]),
            discount_code_id=int(row["discount_code_id"]) if row["discount_code_id"] is not None else None,
            discount_code_text=str(row["discount_code_text"]) if row["discount_code_text"] else None,
            subtotal_amount=str(row["subtotal_amount"]),
            discount_amount=str(row["discount_amount"]),
            total_amount=str(row["total_amount"]),
            currency=str(row["currency"]),
            note=str(row["note"]) if row["note"] else None,
            reviewed_at=str(row["reviewed_at"]) if row["reviewed_at"] else None,
            reviewed_by=int(row["reviewed_by"]) if row["reviewed_by"] is not None else None,
            completed_at=str(row["completed_at"]) if row["completed_at"] else None,
            cancelled_at=str(row["cancelled_at"]) if row["cancelled_at"] else None,
            cancel_reason=str(row["cancel_reason"]) if row["cancel_reason"] else None,
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _map_checkout_item(row: aiosqlite.Row) -> CheckoutOrderItemRecord:
        return CheckoutOrderItemRecord(
            order_id=int(row["order_id"]),
            system_id=int(row["system_id"]),
            system_name=str(row["system_name"]),
            unit_price=str(row["unit_price"]),
            line_total=str(row["line_total"]),
        )
