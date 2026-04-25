from __future__ import annotations

import aiosqlite

from sales_bot.db import Database
from sales_bot.exceptions import NotFoundError, PermissionDeniedError
from sales_bot.models import NotificationRecord


class NotificationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create_notification(
        self,
        *,
        user_id: int,
        title: str,
        body: str,
        link_path: str | None,
        kind: str = "general",
        created_by: int | None = None,
    ) -> NotificationRecord:
        cleaned_title = title.strip()
        cleaned_body = body.strip()
        if not cleaned_title:
            raise PermissionDeniedError("חובה להזין כותרת להתראה.")
        if not cleaned_body:
            raise PermissionDeniedError("חובה להזין תוכן להתראה.")
        notification_id = await self.database.insert(
            """
            INSERT INTO website_notifications (user_id, title, body, link_path, kind, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, cleaned_title, cleaned_body, link_path.strip() if link_path else None, kind.strip() or "general", created_by),
        )
        return await self.get_notification(notification_id)

    async def get_notification(self, notification_id: int) -> NotificationRecord:
        row = await self.database.fetchone("SELECT * FROM website_notifications WHERE id = ?", (notification_id,))
        if row is None:
            raise NotFoundError("ההתראה לא נמצאה.")
        return self._map_notification(row)

    async def list_notifications(self, user_id: int, *, limit: int = 100) -> list[NotificationRecord]:
        rows = await self.database.fetchall(
            "SELECT * FROM website_notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [self._map_notification(row) for row in rows]

    async def list_recent_notifications(self, *, limit: int = 100) -> list[NotificationRecord]:
        rows = await self.database.fetchall(
            "SELECT * FROM website_notifications ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._map_notification(row) for row in rows]

    async def mark_read(self, user_id: int, notification_id: int) -> None:
        row = await self.database.fetchone(
            "SELECT 1 FROM website_notifications WHERE id = ? AND user_id = ?",
            (notification_id, user_id),
        )
        if row is None:
            raise NotFoundError("ההתראה לא נמצאה בחשבון הזה.")
        await self.database.execute(
            "UPDATE website_notifications SET is_read = TRUE, read_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (notification_id, user_id),
        )

    async def mark_all_read(self, user_id: int) -> None:
        await self.database.execute(
            "UPDATE website_notifications SET is_read = TRUE, read_at = CURRENT_TIMESTAMP WHERE user_id = ? AND is_read = FALSE",
            (user_id,),
        )

    async def unread_count(self, user_id: int) -> int:
        row = await self.database.fetchone(
            "SELECT COUNT(*) AS total FROM website_notifications WHERE user_id = ? AND is_read = FALSE",
            (user_id,),
        )
        return int(row["total"]) if row is not None else 0

    @staticmethod
    def _map_notification(row: aiosqlite.Row) -> NotificationRecord:
        return NotificationRecord(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            title=str(row["title"]),
            body=str(row["body"]),
            link_path=str(row["link_path"]) if row["link_path"] else None,
            kind=str(row["kind"]),
            is_read=bool(row["is_read"]),
            created_by=int(row["created_by"]) if row["created_by"] is not None else None,
            created_at=str(row["created_at"]),
            read_at=str(row["read_at"]) if row["read_at"] else None,
        )