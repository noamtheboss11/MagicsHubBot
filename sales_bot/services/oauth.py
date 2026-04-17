from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import aiohttp
import discord

from sales_bot.config import Settings
from sales_bot.db import Database
from sales_bot.exceptions import ConfigurationError, ExternalServiceError, NotFoundError
from sales_bot.models import RobloxLinkRecord, RobloxPublicProfile

if TYPE_CHECKING:
    from sales_bot.bot import SalesBot


LOGGER = logging.getLogger(__name__)


class RobloxOAuthService:
    AUTHORIZATION_ENDPOINT = "https://apis.roblox.com/oauth/v1/authorize"
    TOKEN_ENDPOINT = "https://apis.roblox.com/oauth/v1/token"
    USERINFO_ENDPOINT = "https://apis.roblox.com/oauth/v1/userinfo"
    INVENTORY_OWNERSHIP_ENDPOINT = "https://inventory.roblox.com/v1/users/{user_id}/items/GamePass/{gamepass_id}/is-owned"
    PUBLIC_PROFILE_ENDPOINT = "https://users.roblox.com/v1/users/{user_id}"
    HEADSHOT_ENDPOINT = (
        "https://thumbnails.roblox.com/v1/users/avatar-headshot"
        "?userIds={user_id}&size=420x420&format=Png&isCircular=false"
    )
    STATE_LIFETIME_MINUTES = 60

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def ensure_configured(self) -> None:
        if not self.settings.roblox_oauth_enabled:
            raise ConfigurationError(
                "Roblox OAuth is not configured. Set ROBLOX_CLIENT_ID, ROBLOX_CLIENT_SECRET, "
                "ROBLOX_REDIRECT_URI, ROBLOX_ENTRY_LINK, ROBLOX_PRIVACY_POLICY_URL, and ROBLOX_TERMS_URL."
            )

    async def create_state(self, user_id: int) -> str:
        self.ensure_configured()
        await self.database.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (datetime.now(UTC).isoformat(),))
        state = secrets.token_urlsafe(24)
        expires_at = datetime.now(UTC) + timedelta(minutes=self.STATE_LIFETIME_MINUTES)
        await self.database.execute(
            "INSERT INTO oauth_states (state, user_id, expires_at) VALUES (?, ?, ?)",
            (state, user_id, expires_at.isoformat()),
        )
        LOGGER.info("Created Roblox OAuth state for Discord user %s", user_id)
        return state

    def build_authorization_url(self, state: str) -> str:
        self.ensure_configured()
        params = {
            "client_id": self.settings.roblox_client_id,
            "response_type": "code",
            "scope": "openid profile",
            "redirect_uri": self.settings.roblox_redirect_uri,
            "state": state,
        }
        return f"{self.AUTHORIZATION_ENDPOINT}?{urlencode(params)}"

    async def consume_state(self, state: str) -> int:
        await self.database.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (datetime.now(UTC).isoformat(),))
        row = await self.database.fetchone(
            "SELECT * FROM oauth_states WHERE state = ?",
            (state,),
        )
        if row is None:
            LOGGER.warning("Roblox OAuth state lookup missed for state prefix %s", state[:8])
            raise NotFoundError("OAuth state is invalid or expired. Run /link again and try the new button.")

        expires_at = datetime.fromisoformat(str(row["expires_at"]))
        if expires_at < datetime.now(UTC):
            await self.database.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            LOGGER.warning("Roblox OAuth state expired for Discord user %s", int(row["user_id"]))
            raise NotFoundError("OAuth state is invalid or expired. Run /link again and try the new button.")

        await self.database.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        LOGGER.info("Consumed Roblox OAuth state for Discord user %s", int(row["user_id"]))

        return int(row["user_id"])

    async def exchange_code(self, session: aiohttp.ClientSession, code: str) -> dict[str, Any]:
        self.ensure_configured()
        payload = {
            "grant_type": "authorization_code",
            "client_id": self.settings.roblox_client_id,
            "client_secret": self.settings.roblox_client_secret,
            "code": code,
            "redirect_uri": self.settings.roblox_redirect_uri,
        }
        async with session.post(self.TOKEN_ENDPOINT, data=payload) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise ExternalServiceError(data.get("error_description") or "Roblox token exchange failed.")
            return data

    async def fetch_profile(self, session: aiohttp.ClientSession, access_token: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with session.get(self.USERINFO_ENDPOINT, headers=headers) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise ExternalServiceError(data.get("error_description") or "Roblox profile fetch failed.")
            return data

    async def link_account(self, user_id: int, profile: dict[str, Any]) -> RobloxLinkRecord:
        roblox_sub = str(profile["sub"])
        username = profile.get("preferred_username") or profile.get("nickname")
        display_name = profile.get("name")
        profile_url = f"https://www.roblox.com/users/{roblox_sub}/profile" if roblox_sub.isdigit() else None

        await self.database.execute(
            """
            INSERT INTO roblox_links (user_id, roblox_sub, roblox_username, roblox_display_name, profile_url, raw_profile_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET
                roblox_sub = excluded.roblox_sub,
                roblox_username = excluded.roblox_username,
                roblox_display_name = excluded.roblox_display_name,
                profile_url = excluded.profile_url,
                raw_profile_json = excluded.raw_profile_json,
                linked_at = CURRENT_TIMESTAMP
            """,
            (
                user_id,
                roblox_sub,
                username,
                display_name,
                profile_url,
                json.dumps(profile),
            ),
        )
        return await self.get_link(user_id)

    async def get_link(self, user_id: int) -> RobloxLinkRecord:
        row = await self.database.fetchone(
            "SELECT * FROM roblox_links WHERE user_id = ?",
            (user_id,),
        )
        if row is None:
            raise NotFoundError("No linked Roblox account found for that user.")
        return RobloxLinkRecord(
            user_id=int(row["user_id"]),
            roblox_sub=str(row["roblox_sub"]),
            roblox_username=str(row["roblox_username"]) if row["roblox_username"] else None,
            roblox_display_name=str(row["roblox_display_name"]) if row["roblox_display_name"] else None,
            profile_url=str(row["profile_url"]) if row["profile_url"] else None,
            linked_at=str(row["linked_at"]),
        )

    async def fetch_public_profile(self, session: aiohttp.ClientSession, roblox_user_id: str) -> RobloxPublicProfile:
        if not roblox_user_id.isdigit():
            raise ExternalServiceError("The linked Roblox user ID is invalid.")

        profile_task = self._fetch_json(session, self.PUBLIC_PROFILE_ENDPOINT.format(user_id=roblox_user_id))
        headshot_task = self._fetch_json(session, self.HEADSHOT_ENDPOINT.format(user_id=roblox_user_id))
        profile_data, headshot_data = await asyncio.gather(profile_task, headshot_task)

        created_at_raw = profile_data.get("created")
        age_days: int | None = None
        if created_at_raw:
            created_at = datetime.fromisoformat(str(created_at_raw).replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            age_days = max(0, (datetime.now(UTC) - created_at.astimezone(UTC)).days)

        headshot_url = None
        data_entries = headshot_data.get("data") if isinstance(headshot_data, dict) else None
        if isinstance(data_entries, list) and data_entries:
            first_entry = data_entries[0]
            if isinstance(first_entry, dict):
                headshot_url = first_entry.get("imageUrl")

        return RobloxPublicProfile(
            user_id=int(profile_data["id"]),
            username=str(profile_data.get("name") or profile_data.get("displayName") or roblox_user_id),
            display_name=str(profile_data.get("displayName") or profile_data.get("name") or roblox_user_id),
            description=str(profile_data.get("description") or ""),
            created_at=str(created_at_raw) if created_at_raw else None,
            age_days=age_days,
            headshot_url=str(headshot_url) if headshot_url else None,
            profile_url=f"https://www.roblox.com/users/{roblox_user_id}/profile",
        )

    async def sync_linked_member(
        self,
        bot: "SalesBot",
        user_id: int,
        record: RobloxLinkRecord,
    ) -> list[str]:
        if self.settings.primary_guild_id is None:
            return []

        guild = bot.get_guild(self.settings.primary_guild_id)
        if guild is None:
            try:
                guild = await bot.fetch_guild(self.settings.primary_guild_id)
            except discord.HTTPException:
                return ["I couldn't load the configured guild for Roblox syncing."]

        try:
            member = guild.get_member(user_id)
            if member is None:
                member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            return ["The linked user was not found in the configured guild."]

        sync_notes: list[str] = []
        nickname = self.build_synced_nickname(record.roblox_username, record.roblox_display_name)
        if nickname and getattr(member, "nick", None) != nickname:
            try:
                await member.edit(nick=nickname, reason=f"Roblox account linked for {user_id}")
            except discord.HTTPException:
                sync_notes.append("I couldn't update the member nickname.")

        role = guild.get_role(self.settings.roblox_verified_role_id)
        if role is None:
            sync_notes.append("The configured Roblox verified role was not found in the guild.")
        elif role not in member.roles:
            try:
                await member.add_roles(role, reason=f"Roblox account linked for {user_id}")
            except discord.HTTPException:
                sync_notes.append("I couldn't assign the configured Roblox verified role.")

        return sync_notes

    @staticmethod
    def build_synced_nickname(username: str | None, display_name: str | None) -> str:
        base_name = (username or display_name or "Roblox User").strip()
        display = (display_name or "").strip()
        if display and display.casefold() != base_name.casefold():
            nickname = f"{base_name} ({display})"
        else:
            nickname = base_name

        if len(nickname) <= 32:
            return nickname

        if display and display.casefold() != base_name.casefold():
            available = 32 - len(base_name) - 3
            if available > 0:
                return f"{base_name} ({display[:available]})"
        return base_name[:32]

    async def linked_user_owns_gamepass(
        self,
        session: aiohttp.ClientSession,
        *,
        discord_user_id: int,
        gamepass_id: str,
    ) -> bool:
        link_record = await self.get_link(discord_user_id)
        url = self.INVENTORY_OWNERSHIP_ENDPOINT.format(
            user_id=link_record.roblox_sub,
            gamepass_id=gamepass_id,
        )
        async with session.get(url) as response:
            if response.status >= 400:
                raise ExternalServiceError("לא הצלחתי לבדוק אם המשתמש מחזיק בגיימפאס הזה ברובלוקס.")

            data = await response.json(content_type=None)
            return bool(data)

    async def _fetch_json(self, session: aiohttp.ClientSession, url: str) -> dict[str, Any]:
        async with session.get(url) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                message = data.get("errors") if isinstance(data, dict) else None
                raise ExternalServiceError(str(message) if message else "Roblox profile lookup failed.")
            if not isinstance(data, dict):
                raise ExternalServiceError("Roblox returned an unexpected profile response.")
            return data