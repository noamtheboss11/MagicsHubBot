from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite


class Database:
    def __init__(self, path: Path, schema_path: Path) -> None:
        self.path = path
        self.schema_path = schema_path
        self._connection: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database connection has not been initialized")
        return self._connection

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.path)
        self._connection.row_factory = aiosqlite.Row
        schema = self.schema_path.read_text(encoding="utf-8")
        await self._connection.executescript(schema)
        await self._run_migrations()
        await self._connection.commit()

    async def _run_migrations(self) -> None:
        await self._ensure_column("systems", "roblox_gamepass_id", "TEXT")

    async def _ensure_column(self, table_name: str, column_name: str, column_sql: str) -> None:
        rows = await self.fetchall(f"PRAGMA table_info({table_name})")
        if any(str(row["name"]) == column_name for row in rows):
            return
        await self.connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def execute(self, query: str, parameters: Sequence[Any] = ()) -> None:
        await self.connection.execute(query, parameters)
        await self.connection.commit()

    async def executemany(self, query: str, parameters: Iterable[Sequence[Any]]) -> None:
        await self.connection.executemany(query, parameters)
        await self.connection.commit()

    async def fetchone(self, query: str, parameters: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self.connection.execute(query, parameters) as cursor:
            return await cursor.fetchone()

    async def fetchall(self, query: str, parameters: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self.connection.execute(query, parameters) as cursor:
            return await cursor.fetchall()

    async def insert(self, query: str, parameters: Sequence[Any] = ()) -> int:
        cursor = await self.connection.execute(query, parameters)
        await self.connection.commit()
        return int(cursor.lastrowid)
