from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import aiosqlite

from sales_bot.db import Database
from sales_bot.exceptions import NotFoundError, PermissionDeniedError
from sales_bot.models import AdminPanelSessionRecord


class AdminPanelService:
    def __init__(self, database: Database, lifetime_minutes: int) -> None:
        self.database = database
        self.lifetime_minutes = max(5, lifetime_minutes)

    async def create_session(
        self,
        *,
        admin_user_id: int,
        panel_type: str,
        target_id: int | None = None,
    ) -> AdminPanelSessionRecord:
        await self.cleanup_expired_sessions()
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(minutes=self.lifetime_minutes)
        await self.database.execute(
            """
            INSERT INTO admin_panel_sessions (token, admin_user_id, panel_type, target_id, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token, admin_user_id, panel_type, target_id, expires_at),
        )
        return await self.get_session(token)

    async def get_session(
        self,
        token: str,
        *,
        expected_panel_type: str | None = None,
    ) -> AdminPanelSessionRecord:
        await self.cleanup_expired_sessions()
        row = await self.database.fetchone(
            "SELECT * FROM admin_panel_sessions WHERE token = ?",
            (token,),
        )
        if row is None:
            raise NotFoundError("This admin panel link is invalid or has expired.")

        record = self._map_session(row)
        if expected_panel_type and record.panel_type != expected_panel_type:
            raise PermissionDeniedError("This admin panel link is for a different action.")
        return record

    async def cleanup_expired_sessions(self) -> None:
        await self.database.execute(
            "DELETE FROM admin_panel_sessions WHERE expires_at <= ?",
            (datetime.now(UTC),),
        )

    @staticmethod
    def _map_session(row: aiosqlite.Row) -> AdminPanelSessionRecord:
        return AdminPanelSessionRecord(
            token=str(row["token"]),
            admin_user_id=int(row["admin_user_id"]),
            panel_type=str(row["panel_type"]),
            target_id=int(row["target_id"]) if row["target_id"] is not None else None,
            expires_at=str(row["expires_at"]),
            created_at=str(row["created_at"]),
        )