from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import aiohttp

from sales_bot.config import Settings
from sales_bot.db import Database
from sales_bot.exceptions import ConfigurationError, ExternalServiceError, NotFoundError, PermissionDeniedError
from sales_bot.models import RobloxGamePassRecord, RobloxOwnerLinkRecord

if TYPE_CHECKING:
    from sales_bot.bot import SalesBot


LOGGER = logging.getLogger(__name__)


class RobloxCreatorService:
    AUTHORIZATION_ENDPOINT = "https://apis.roblox.com/oauth/v1/authorize"
    TOKEN_ENDPOINT = "https://apis.roblox.com/oauth/v1/token"
    USERINFO_ENDPOINT = "https://apis.roblox.com/oauth/v1/userinfo"
    CREATE_GAMEPASS_ENDPOINT = "https://apis.roblox.com/game-passes/v1/universes/{universe_id}/game-passes"
    LIST_GAMEPASSES_ENDPOINT = "https://apis.roblox.com/game-passes/v1/universes/{universe_id}/game-passes/creator"
    GET_GAMEPASS_ENDPOINT = "https://apis.roblox.com/game-passes/v1/universes/{universe_id}/game-passes/{game_pass_id}/creator"
    UPDATE_GAMEPASS_ENDPOINT = "https://apis.roblox.com/game-passes/v1/universes/{universe_id}/game-passes/{game_pass_id}"
    OAUTH_SCOPES = ("openid", "profile", "game-pass:read", "game-pass:write")
    STATE_LIFETIME_MINUTES = 60
    TOKEN_REFRESH_GRACE_SECONDS = 60

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def ensure_oauth_configured(self) -> None:
        if not self.settings.roblox_owner_oauth_enabled:
            raise ConfigurationError(
                "Roblox owner OAuth is not configured. Set ROBLOX_OWNER_CLIENT_ID, "
                "ROBLOX_OWNER_CLIENT_SECRET, and ROBLOX_OWNER_REDIRECT_URI."
            )

    def ensure_gamepass_management_configured(self) -> None:
        self.ensure_oauth_configured()
        if self.settings.roblox_owner_universe_id is None:
            raise ConfigurationError(
                "Roblox owner game pass management is not fully configured. Set ROBLOX_OWNER_UNIVERSE_ID."
            )

    async def create_state(self, guild_id: int, discord_user_id: int) -> str:
        self.ensure_oauth_configured()
        await self.database.execute(
            "DELETE FROM roblox_owner_states WHERE expires_at <= ?",
            (datetime.now(UTC).isoformat(),),
        )
        state = secrets.token_urlsafe(24)
        expires_at = datetime.now(UTC) + timedelta(minutes=self.STATE_LIFETIME_MINUTES)
        await self.database.execute(
            """
            INSERT INTO roblox_owner_states (state, guild_id, user_id, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (state, guild_id, discord_user_id, expires_at.isoformat()),
        )
        LOGGER.info("Created Roblox owner OAuth state for guild %s and Discord user %s", guild_id, discord_user_id)
        return state

    def build_authorization_url(self, state: str) -> str:
        self.ensure_oauth_configured()
        params = {
            "client_id": self.settings.roblox_owner_client_id,
            "response_type": "code",
            "scope": " ".join(self.OAUTH_SCOPES),
            "redirect_uri": self.settings.roblox_owner_redirect_uri,
            "state": state,
            "nonce": state,
        }
        return f"{self.AUTHORIZATION_ENDPOINT}?{urlencode(params)}"

    async def consume_state(self, state: str) -> tuple[int, int]:
        await self.database.execute(
            "DELETE FROM roblox_owner_states WHERE expires_at <= ?",
            (datetime.now(UTC).isoformat(),),
        )
        row = await self.database.fetchone(
            "SELECT * FROM roblox_owner_states WHERE state = ?",
            (state,),
        )
        if row is None:
            raise NotFoundError("Owner OAuth state is invalid or expired. Run /linkasowner again.")

        expires_at = self._parse_datetime(row["expires_at"])
        if expires_at < datetime.now(UTC):
            await self.database.execute("DELETE FROM roblox_owner_states WHERE state = ?", (state,))
            raise NotFoundError("Owner OAuth state is invalid or expired. Run /linkasowner again.")

        await self.database.execute("DELETE FROM roblox_owner_states WHERE state = ?", (state,))
        return int(row["guild_id"]), int(row["user_id"])

    async def exchange_code(self, session: aiohttp.ClientSession | None, code: str) -> dict[str, Any]:
        self.ensure_oauth_configured()
        if session is None:
            raise ExternalServiceError("The bot HTTP session is not ready yet. Try again in a moment.")

        payload = {
            "grant_type": "authorization_code",
            "client_id": self.settings.roblox_owner_client_id,
            "client_secret": self.settings.roblox_owner_client_secret,
            "code": code,
            "redirect_uri": self.settings.roblox_owner_redirect_uri,
        }
        async with session.post(self.TOKEN_ENDPOINT, data=payload) as response:
            data = await self._read_response_data(response)
            if response.status >= 400:
                raise ExternalServiceError(
                    self._roblox_error_message(data, "Roblox owner token exchange failed.")
                )
            if not isinstance(data, dict):
                raise ExternalServiceError("Roblox returned an unexpected owner token response.")
            return data

    async def refresh_tokens(self, session: aiohttp.ClientSession | None, refresh_token: str) -> dict[str, Any]:
        self.ensure_oauth_configured()
        if session is None:
            raise ExternalServiceError("The bot HTTP session is not ready yet. Try again in a moment.")

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.settings.roblox_owner_client_id,
            "client_secret": self.settings.roblox_owner_client_secret,
        }
        async with session.post(self.TOKEN_ENDPOINT, data=payload) as response:
            data = await self._read_response_data(response)
            if response.status >= 400:
                raise ExternalServiceError(
                    self._roblox_error_message(
                        data,
                        "Roblox owner tokens could not be refreshed. Run /linkasowner again.",
                    )
                )
            if not isinstance(data, dict):
                raise ExternalServiceError("Roblox returned an unexpected refresh token response.")
            return data

    async def fetch_profile(self, session: aiohttp.ClientSession | None, access_token: str) -> dict[str, Any]:
        if session is None:
            raise ExternalServiceError("The bot HTTP session is not ready yet. Try again in a moment.")

        headers = {"Authorization": f"Bearer {access_token}"}
        async with session.get(self.USERINFO_ENDPOINT, headers=headers) as response:
            data = await self._read_response_data(response)
            if response.status >= 400:
                raise ExternalServiceError(
                    self._roblox_error_message(data, "Roblox owner profile fetch failed.")
                )
            if not isinstance(data, dict):
                raise ExternalServiceError("Roblox returned an unexpected owner profile response.")
            return data

    async def link_owner(
        self,
        guild_id: int,
        discord_user_id: int,
        profile: dict[str, Any],
        tokens: dict[str, Any],
    ) -> RobloxOwnerLinkRecord:
        roblox_sub = str(profile.get("sub") or "").strip()
        if not roblox_sub:
            raise ExternalServiceError("Roblox did not return a valid owner account identifier.")

        access_token = str(tokens.get("access_token") or "").strip()
        refresh_token = str(tokens.get("refresh_token") or "").strip()
        if not access_token or not refresh_token:
            raise ExternalServiceError("Roblox did not return the required owner access tokens.")

        username = profile.get("preferred_username") or profile.get("nickname")
        display_name = profile.get("name")
        profile_url = f"https://www.roblox.com/users/{roblox_sub}/profile" if roblox_sub.isdigit() else None
        token_type = str(tokens.get("token_type") or "Bearer")
        scope = str(tokens.get("scope") or " ".join(self.OAUTH_SCOPES))
        expires_in = self._coerce_positive_int(tokens.get("expires_in"), fallback=900)
        token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

        await self.database.execute(
            """
            INSERT INTO roblox_owner_links (
                guild_id,
                discord_user_id,
                roblox_sub,
                roblox_username,
                roblox_display_name,
                profile_url,
                raw_profile_json,
                access_token,
                refresh_token,
                token_type,
                scope,
                token_expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET
                discord_user_id = excluded.discord_user_id,
                roblox_sub = excluded.roblox_sub,
                roblox_username = excluded.roblox_username,
                roblox_display_name = excluded.roblox_display_name,
                profile_url = excluded.profile_url,
                raw_profile_json = excluded.raw_profile_json,
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                token_type = excluded.token_type,
                scope = excluded.scope,
                token_expires_at = excluded.token_expires_at,
                linked_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                guild_id,
                discord_user_id,
                roblox_sub,
                username,
                display_name,
                profile_url,
                json.dumps(profile),
                access_token,
                refresh_token,
                token_type,
                scope,
                token_expires_at.isoformat(),
            ),
        )
        LOGGER.info("Linked Roblox owner access for guild %s using Discord user %s", guild_id, discord_user_id)
        return await self.get_link(guild_id)

    async def get_link(self, guild_id: int) -> RobloxOwnerLinkRecord:
        row = await self._get_link_row(guild_id)
        return self._map_owner_link(row)

    async def get_link_for_user(self, guild_id: int, discord_user_id: int) -> RobloxOwnerLinkRecord:
        record = await self.get_link(guild_id)
        if record.discord_user_id != discord_user_id:
            raise PermissionDeniedError(
                "This server already has Roblox owner access linked by a different Discord account. "
                "Run /linkasowner again as the current server owner to replace it."
            )
        return record

    async def list_gamepasses(
        self,
        bot: "SalesBot",
        guild_id: int,
        discord_user_id: int,
    ) -> list[RobloxGamePassRecord]:
        self.ensure_gamepass_management_configured()
        gamepasses: list[RobloxGamePassRecord] = []
        page_token: str | None = None

        while True:
            params: dict[str, str | int] = {"pageSize": 100}
            if page_token:
                params["pageToken"] = page_token

            data = await self._authorized_request(
                bot,
                guild_id=guild_id,
                discord_user_id=discord_user_id,
                method="GET",
                url=self.LIST_GAMEPASSES_ENDPOINT.format(universe_id=self.settings.roblox_owner_universe_id),
                params=params,
            )
            if not isinstance(data, dict):
                raise ExternalServiceError("Roblox returned an unexpected game pass list response.")

            raw_gamepasses = data.get("gamePasses")
            if not isinstance(raw_gamepasses, list):
                raise ExternalServiceError("Roblox returned an invalid game pass list response.")

            gamepasses.extend(self._map_gamepass(item) for item in raw_gamepasses if isinstance(item, dict))
            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break
            page_token = str(next_page_token)

        return sorted(gamepasses, key=lambda item: (item.name.lower(), item.game_pass_id))

    async def get_gamepass(
        self,
        bot: "SalesBot",
        guild_id: int,
        discord_user_id: int,
        game_pass_id: int,
    ) -> RobloxGamePassRecord:
        self.ensure_gamepass_management_configured()
        data = await self._authorized_request(
            bot,
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            method="GET",
            url=self.GET_GAMEPASS_ENDPOINT.format(
                universe_id=self.settings.roblox_owner_universe_id,
                game_pass_id=game_pass_id,
            ),
        )
        if not isinstance(data, dict):
            raise ExternalServiceError("Roblox returned an unexpected game pass response.")
        return self._map_gamepass(data)

    async def create_gamepass(
        self,
        bot: "SalesBot",
        guild_id: int,
        discord_user_id: int,
        *,
        name: str,
        description: str | None,
        price: int | None,
        is_for_sale: bool,
        is_regional_pricing_enabled: bool,
        image_upload: tuple[str, bytes, str | None] | None = None,
    ) -> RobloxGamePassRecord:
        self.ensure_gamepass_management_configured()
        form = aiohttp.FormData()
        form.add_field("name", name.strip())
        if description is not None:
            form.add_field("description", description.strip())
        form.add_field("isForSale", self._bool_to_form_value(is_for_sale))
        if price is not None:
            form.add_field("price", str(price))
        form.add_field("isRegionalPricingEnabled", self._bool_to_form_value(is_regional_pricing_enabled))
        if image_upload is not None:
            image_name, image_bytes, content_type = image_upload
            form.add_field(
                "imageFile",
                image_bytes,
                filename=image_name,
                content_type=content_type or "application/octet-stream",
            )

        data = await self._authorized_request(
            bot,
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            method="POST",
            url=self.CREATE_GAMEPASS_ENDPOINT.format(universe_id=self.settings.roblox_owner_universe_id),
            data=form,
        )
        if not isinstance(data, dict):
            raise ExternalServiceError("Roblox returned an unexpected game pass creation response.")
        return self._map_gamepass(data)

    async def update_gamepass(
        self,
        bot: "SalesBot",
        guild_id: int,
        discord_user_id: int,
        *,
        game_pass_id: int,
        name: str | None,
        description: str | None,
        price: int | None,
        is_for_sale: bool | None,
        is_regional_pricing_enabled: bool | None,
        image_upload: tuple[str, bytes, str | None] | None = None,
    ) -> RobloxGamePassRecord:
        self.ensure_gamepass_management_configured()
        form = aiohttp.FormData()
        changed = False

        if name is not None:
            form.add_field("name", name.strip())
            changed = True
        if description is not None:
            form.add_field("description", description.strip())
            changed = True
        if price is not None:
            form.add_field("price", str(price))
            changed = True
        if is_for_sale is not None:
            form.add_field("isForSale", self._bool_to_form_value(is_for_sale))
            changed = True
        if is_regional_pricing_enabled is not None:
            form.add_field("isRegionalPricingEnabled", self._bool_to_form_value(is_regional_pricing_enabled))
            changed = True
        if image_upload is not None:
            image_name, image_bytes, content_type = image_upload
            form.add_field(
                "file",
                image_bytes,
                filename=image_name,
                content_type=content_type or "application/octet-stream",
            )
            changed = True

        if not changed:
            raise ConfigurationError("Provide at least one game pass field to update.")

        await self._authorized_request(
            bot,
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            method="PATCH",
            url=self.UPDATE_GAMEPASS_ENDPOINT.format(
                universe_id=self.settings.roblox_owner_universe_id,
                game_pass_id=game_pass_id,
            ),
            data=form,
            allow_no_content=True,
        )
        return await self.get_gamepass(bot, guild_id, discord_user_id, game_pass_id)

    @staticmethod
    def gamepass_url(game_pass_id: int) -> str:
        return f"https://www.roblox.com/game-pass/{game_pass_id}"

    async def _authorized_request(
        self,
        bot: "SalesBot",
        *,
        guild_id: int,
        discord_user_id: int,
        method: str,
        url: str,
        params: dict[str, str | int] | None = None,
        data: Any = None,
        allow_no_content: bool = False,
    ) -> dict[str, Any] | list[Any] | str | None:
        session = bot.http_session
        if session is None:
            raise ExternalServiceError("The bot HTTP session is not ready yet. Try again in a moment.")

        access_token = await self._get_access_token(bot, guild_id, discord_user_id)
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {access_token}"}
            async with session.request(method, url, headers=headers, params=params, data=data) as response:
                response_data = await self._read_response_data(response)
                if allow_no_content and response.status == 204:
                    return None
                if response.status == 401 and attempt == 0:
                    access_token = await self._get_access_token(bot, guild_id, discord_user_id, force_refresh=True)
                    continue
                if response.status >= 400:
                    raise ExternalServiceError(
                        self._roblox_error_message(response_data, f"Roblox request failed for {method} {url}.")
                    )
                return response_data

        raise ExternalServiceError("Roblox authorization failed. Run /linkasowner again.")

    async def _get_access_token(
        self,
        bot: "SalesBot",
        guild_id: int,
        discord_user_id: int,
        *,
        force_refresh: bool = False,
    ) -> str:
        row = await self._get_link_row(guild_id)
        if int(row["discord_user_id"]) != discord_user_id:
            raise PermissionDeniedError(
                "This server already has Roblox owner access linked by a different Discord account. "
                "Run /linkasowner again as the current server owner to replace it."
            )

        expires_at = self._parse_datetime(row["token_expires_at"])
        if force_refresh or expires_at <= datetime.now(UTC) + timedelta(seconds=self.TOKEN_REFRESH_GRACE_SECONDS):
            refresh_token = str(row["refresh_token"])
            try:
                tokens = await self.refresh_tokens(bot.http_session, refresh_token)
            except ExternalServiceError as exc:
                raise PermissionDeniedError(
                    "Roblox owner access expired or was revoked. Run /linkasowner again."
                ) from exc

            await self._store_refreshed_tokens(guild_id, tokens, fallback_refresh_token=refresh_token)
            row = await self._get_link_row(guild_id)

        return str(row["access_token"])

    async def _store_refreshed_tokens(
        self,
        guild_id: int,
        tokens: dict[str, Any],
        *,
        fallback_refresh_token: str,
    ) -> None:
        access_token = str(tokens.get("access_token") or "").strip()
        refresh_token = str(tokens.get("refresh_token") or fallback_refresh_token).strip()
        if not access_token or not refresh_token:
            raise ExternalServiceError("Roblox did not return refreshed owner tokens.")

        token_type = str(tokens.get("token_type") or "Bearer")
        scope = str(tokens.get("scope") or " ".join(self.OAUTH_SCOPES))
        expires_in = self._coerce_positive_int(tokens.get("expires_in"), fallback=900)
        token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

        await self.database.execute(
            """
            UPDATE roblox_owner_links
            SET access_token = ?,
                refresh_token = ?,
                token_type = ?,
                scope = ?,
                token_expires_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE guild_id = ?
            """,
            (
                access_token,
                refresh_token,
                token_type,
                scope,
                token_expires_at.isoformat(),
                guild_id,
            ),
        )

    async def _get_link_row(self, guild_id: int) -> Any:
        row = await self.database.fetchone(
            "SELECT * FROM roblox_owner_links WHERE guild_id = ?",
            (guild_id,),
        )
        if row is None:
            raise NotFoundError("This server does not have Roblox owner access linked yet. Use /linkasowner first.")
        return row

    def _map_owner_link(self, row: Any) -> RobloxOwnerLinkRecord:
        return RobloxOwnerLinkRecord(
            guild_id=int(row["guild_id"]),
            discord_user_id=int(row["discord_user_id"]),
            roblox_sub=str(row["roblox_sub"]),
            roblox_username=str(row["roblox_username"]) if row["roblox_username"] else None,
            roblox_display_name=str(row["roblox_display_name"]) if row["roblox_display_name"] else None,
            profile_url=str(row["profile_url"]) if row["profile_url"] else None,
            token_type=str(row["token_type"]),
            scope=str(row["scope"]),
            token_expires_at=str(row["token_expires_at"]),
            linked_at=str(row["linked_at"]),
        )

    def _map_gamepass(self, data: dict[str, Any]) -> RobloxGamePassRecord:
        price_information = data.get("priceInformation")
        price_in_robux: int | None = None
        if isinstance(price_information, dict) and price_information.get("defaultPriceInRobux") is not None:
            price_in_robux = int(price_information["defaultPriceInRobux"])

        icon_asset_id = data.get("iconAssetId")
        return RobloxGamePassRecord(
            game_pass_id=int(data["gamePassId"]),
            name=str(data.get("name") or "Unnamed Game Pass"),
            description=str(data.get("description") or ""),
            is_for_sale=bool(data.get("isForSale")),
            icon_asset_id=int(icon_asset_id) if icon_asset_id is not None else None,
            price_in_robux=price_in_robux,
            created_at=str(data.get("createdTimestamp") or ""),
            updated_at=str(data.get("updatedTimestamp") or ""),
        )

    @staticmethod
    async def _read_response_data(response: aiohttp.ClientResponse) -> dict[str, Any] | list[Any] | str | None:
        if response.status == 204:
            return None

        raw_text = await response.text()
        if not raw_text:
            return None

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            return raw_text

    @staticmethod
    def _roblox_error_message(data: dict[str, Any] | list[Any] | str | None, default: str) -> str:
        if isinstance(data, dict):
            for key in ("errorMessage", "message", "error_description", "hint"):
                value = data.get(key)
                if value:
                    return str(value)

            errors = data.get("errors")
            if isinstance(errors, list) and errors:
                first_error = errors[0]
                if isinstance(first_error, dict):
                    for key in ("message", "userFacingMessage", "detail", "errorMessage"):
                        value = first_error.get(key)
                        if value:
                            return str(value)
                return str(first_error)

        if isinstance(data, list) and data:
            return str(data[0])
        if isinstance(data, str) and data.strip():
            return data.strip()
        return default

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _coerce_positive_int(value: Any, *, fallback: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return fallback
        return parsed if parsed > 0 else fallback

    @staticmethod
    def _bool_to_form_value(value: bool) -> str:
        return "true" if value else "false"