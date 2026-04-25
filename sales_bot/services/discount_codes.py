from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

import aiosqlite

from sales_bot.db import Database
from sales_bot.exceptions import AlreadyExistsError, NotFoundError, PermissionDeniedError
from sales_bot.models import CartItemRecord, DiscountCodeRecord


class DiscountCodeService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create_code(
        self,
        *,
        code: str,
        description: str | None,
        discount_type: str,
        amount: str,
        currency: str | None,
        system_id: int | None,
        max_redemptions: int | None,
        per_user_limit: int,
        expires_at: str | None,
        created_by: int | None,
    ) -> DiscountCodeRecord:
        normalized_code = self._normalize_code(code)
        normalized_type = self._normalize_type(discount_type)
        normalized_amount = self._normalize_amount(amount, normalized_type)
        normalized_currency = self._normalize_currency(currency) if normalized_type == "fixed" else None
        normalized_expires_at = self._normalize_expires_at(expires_at)
        normalized_description = str(description or "").strip() or None
        if per_user_limit <= 0:
            raise PermissionDeniedError("מגבלת שימוש למשתמש חייבת להיות גדולה מאפס.")
        if max_redemptions is not None and max_redemptions <= 0:
            raise PermissionDeniedError("מספר המימושים הכולל חייב להיות גדול מאפס.")

        try:
            code_id = await self.database.insert(
                """
                INSERT INTO discount_codes (
                    code,
                    description,
                    discount_type,
                    amount,
                    currency,
                    system_id,
                    max_redemptions,
                    per_user_limit,
                    expires_at,
                    created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_code,
                    normalized_description,
                    normalized_type,
                    normalized_amount,
                    normalized_currency,
                    system_id,
                    max_redemptions,
                    per_user_limit,
                    normalized_expires_at,
                    created_by,
                ),
            )
        except aiosqlite.IntegrityError as exc:
            raise AlreadyExistsError("קוד ההנחה הזה כבר קיים.") from exc

        return await self.get_code(code_id)

    async def list_codes(self) -> list[DiscountCodeRecord]:
        rows = await self.database.fetchall(
            "SELECT * FROM discount_codes ORDER BY is_active DESC, created_at DESC, code ASC"
        )
        return [self._map_code(row) for row in rows]

    async def get_code(self, code_id: int) -> DiscountCodeRecord:
        row = await self.database.fetchone("SELECT * FROM discount_codes WHERE id = ?", (code_id,))
        if row is None:
            raise NotFoundError("קוד ההנחה לא נמצא.")
        return self._map_code(row)

    async def get_code_optional(self, code_text: str) -> DiscountCodeRecord | None:
        normalized_code = self._normalize_code(code_text)
        row = await self.database.fetchone("SELECT * FROM discount_codes WHERE code = ?", (normalized_code,))
        return self._map_code(row) if row is not None else None

    async def set_active(self, code_id: int, is_active: bool) -> DiscountCodeRecord:
        await self.database.execute(
            "UPDATE discount_codes SET is_active = ? WHERE id = ?",
            (bool(is_active), code_id),
        )
        return await self.get_code(code_id)

    async def delete_code(self, code_id: int) -> None:
        code = await self.get_code(code_id)
        await self.database.execute("DELETE FROM discount_codes WHERE id = ?", (code.id,))

    async def preview_discount(self, user_id: int, code_text: str, items: list[CartItemRecord]) -> tuple[DiscountCodeRecord, str]:
        if not items:
            raise PermissionDeniedError("העגלה שלך ריקה כרגע, אז אין מה להחיל עליו קוד.")

        code = await self.get_code_optional(code_text)
        if code is None:
            raise NotFoundError("קוד ההנחה שהוזן לא קיים.")
        if not code.is_active:
            raise PermissionDeniedError("קוד ההנחה הזה לא פעיל כרגע.")
        if code.expires_at and self._is_expired(code.expires_at):
            raise PermissionDeniedError("קוד ההנחה הזה כבר פג תוקף.")

        redemption_count = await self._count_redemptions(code.id)
        if code.max_redemptions is not None and redemption_count >= code.max_redemptions:
            raise PermissionDeniedError("קוד ההנחה הזה מיצה את כל השימושים שלו.")

        user_redemptions = await self._count_redemptions(code.id, user_id=user_id)
        if user_redemptions >= code.per_user_limit:
            raise PermissionDeniedError("כבר ניצלת את כל המימושים שמותרים לקוד הזה בחשבון שלך.")

        applicable_items = items
        if code.system_id is not None:
            applicable_items = [item for item in items if item.system.id == code.system_id]
            if not applicable_items:
                raise PermissionDeniedError("קוד ההנחה הזה לא מתאים למערכות שנמצאות כרגע בעגלה שלך.")

        target_total = Decimal("0.00")
        currency = applicable_items[0].system.website_currency
        for item in applicable_items:
            if item.system.website_currency != currency:
                raise PermissionDeniedError("אי אפשר להחיל כרגע קוד על עגלה שמכילה כמה מטבעות שונים.")
            target_total += Decimal(item.system.website_price or "0")

        if code.discount_type == "percent":
            discount_amount = (target_total * Decimal(code.amount) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            code_currency = (code.currency or currency).upper()
            if code_currency != currency:
                raise PermissionDeniedError("מטבע קוד ההנחה לא תואם למטבע של המערכות בעגלה.")
            discount_amount = min(target_total, Decimal(code.amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if discount_amount <= 0:
            raise PermissionDeniedError("הקוד הזה לא נותן הנחה אפקטיבית על העגלה הנוכחית.")
        return code, format(discount_amount, "f")

    async def record_redemption(self, discount_code_id: int, user_id: int, order_id: int, discount_amount: str) -> None:
        await self.database.execute(
            """
            INSERT INTO discount_code_redemptions (discount_code_id, user_id, order_id, discount_amount)
            VALUES (?, ?, ?, ?)
            """,
            (discount_code_id, user_id, order_id, discount_amount),
        )

    async def _count_redemptions(self, discount_code_id: int, *, user_id: int | None = None) -> int:
        if user_id is None:
            row = await self.database.fetchone(
                "SELECT COUNT(*) AS total FROM discount_code_redemptions WHERE discount_code_id = ?",
                (discount_code_id,),
            )
        else:
            row = await self.database.fetchone(
                "SELECT COUNT(*) AS total FROM discount_code_redemptions WHERE discount_code_id = ? AND user_id = ?",
                (discount_code_id, user_id),
            )
        return int(row["total"]) if row is not None else 0

    @staticmethod
    def _normalize_code(code_text: str) -> str:
        normalized = str(code_text or "").strip().upper()
        if not normalized:
            raise PermissionDeniedError("חובה להזין קוד הנחה.")
        if not re.fullmatch(r"[A-Z0-9_-]{3,32}", normalized):
            raise PermissionDeniedError("קוד ההנחה יכול להכיל רק אותיות באנגלית, מספרים, מקף וקו תחתון.")
        return normalized

    @staticmethod
    def _normalize_type(discount_type: str) -> str:
        normalized = str(discount_type or "").strip().lower()
        if normalized not in {"percent", "fixed"}:
            raise PermissionDeniedError("סוג ההנחה חייב להיות percent או fixed.")
        return normalized

    @staticmethod
    def _normalize_currency(currency_text: str | None) -> str | None:
        cleaned = str(currency_text or "").strip().upper()
        if not cleaned:
            return None
        if not re.fullmatch(r"[A-Z]{3}", cleaned):
            raise PermissionDeniedError("מטבע חייב להיות קוד בן 3 אותיות כמו USD או ILS.")
        return cleaned

    @staticmethod
    def _normalize_amount(amount_text: str, discount_type: str) -> str:
        cleaned = str(amount_text or "").strip().replace(",", "")
        if not cleaned:
            raise PermissionDeniedError("חובה להזין סכום הנחה.")
        try:
            amount = Decimal(cleaned)
        except InvalidOperation as exc:
            raise PermissionDeniedError("סכום ההנחה חייב להיות מספר תקין.") from exc

        if amount <= 0:
            raise PermissionDeniedError("סכום ההנחה חייב להיות גדול מאפס.")
        if discount_type == "percent" and amount >= 100:
            raise PermissionDeniedError("הנחת אחוזים חייבת להיות קטנה מ-100.")
        return format(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")

    @staticmethod
    def _normalize_expires_at(expires_at_text: str | None) -> str | None:
        cleaned = str(expires_at_text or "").strip()
        if not cleaned:
            return None
        cleaned = cleaned.replace("T", " ")
        try:
            datetime.fromisoformat(cleaned)
        except ValueError as exc:
            raise PermissionDeniedError("תאריך התפוגה שנשלח לא תקין.") from exc
        return cleaned

    @staticmethod
    def _is_expired(expires_at_text: str) -> bool:
        return datetime.utcnow() > datetime.fromisoformat(expires_at_text)

    @staticmethod
    def _map_code(row: aiosqlite.Row | None) -> DiscountCodeRecord:
        if row is None:
            raise NotFoundError("קוד ההנחה לא נמצא.")
        return DiscountCodeRecord(
            id=int(row["id"]),
            code=str(row["code"]),
            description=str(row["description"]) if row["description"] else None,
            discount_type=str(row["discount_type"]),
            amount=str(row["amount"]),
            currency=str(row["currency"]).upper() if row["currency"] else None,
            system_id=int(row["system_id"]) if row["system_id"] is not None else None,
            max_redemptions=int(row["max_redemptions"]) if row["max_redemptions"] is not None else None,
            per_user_limit=int(row["per_user_limit"]),
            is_active=bool(row["is_active"]),
            expires_at=str(row["expires_at"]) if row["expires_at"] else None,
            created_by=int(row["created_by"]) if row["created_by"] is not None else None,
            created_at=str(row["created_at"]),
        )