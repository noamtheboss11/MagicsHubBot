from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sales_bot.bot import SalesBot
from sales_bot.checks import guild_owner_only
from sales_bot.exceptions import ConfigurationError, ExternalServiceError, NotFoundError
from sales_bot.models import RobloxGamePassRecord, SystemRecord
from sales_bot.ui.common import defer_interaction_response


async def owner_system_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, SalesBot):
        return []

    systems = await bot.services.systems.search_systems(current)
    return [app_commands.Choice(name=system.name, value=system.name) for system in systems[:25]]


async def owner_gamepass_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, SalesBot) or interaction.guild is None:
        return []

    try:
        gamepasses = await bot.services.roblox_creator.list_gamepasses(
            bot,
            interaction.guild.id,
            interaction.user.id,
        )
    except Exception:
        return []

    normalized = current.casefold().strip()
    filtered = [
        gamepass
        for gamepass in gamepasses
        if not normalized
        or normalized in gamepass.name.casefold()
        or normalized in str(gamepass.game_pass_id)
    ]

    choices: list[app_commands.Choice[str]] = []
    for gamepass in filtered[:25]:
        price_label = f"{gamepass.price_in_robux} R$" if gamepass.price_in_robux is not None else "No price"
        sale_label = "on sale" if gamepass.is_for_sale else "off sale"
        choice_name = f"{gamepass.name} [{price_label}, {sale_label}]"
        choices.append(app_commands.Choice(name=choice_name[:100], value=str(gamepass.game_pass_id)))
    return choices


async def _linked_system_for_gamepass(bot: SalesBot, game_pass_id: int) -> SystemRecord | None:
    try:
        return await bot.services.systems.get_system_by_gamepass_id(str(game_pass_id))
    except NotFoundError:
        return None


def _gamepass_price_label(gamepass: RobloxGamePassRecord) -> str:
    return f"{gamepass.price_in_robux} Robux" if gamepass.price_in_robux is not None else "Not priced"


def _build_gamepass_embed(gamepass: RobloxGamePassRecord, linked_system: SystemRecord | None) -> discord.Embed:
    embed = discord.Embed(
        title=gamepass.name,
        description=gamepass.description or "No description set for this game pass.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Game Pass ID", value=str(gamepass.game_pass_id), inline=True)
    embed.add_field(name="Price", value=_gamepass_price_label(gamepass), inline=True)
    embed.add_field(name="For Sale", value="Yes" if gamepass.is_for_sale else "No", inline=True)
    embed.add_field(
        name="Purchase Link",
        value=f"https://www.roblox.com/game-pass/{gamepass.game_pass_id}",
        inline=False,
    )
    embed.add_field(
        name="Linked System",
        value=linked_system.name if linked_system is not None else "Not linked yet",
        inline=False,
    )
    return embed


class RobloxOwnerCog(commands.Cog):
    def __init__(self, bot: SalesBot) -> None:
        self.bot = bot

    @app_commands.command(name="linkasowner", description="Server owner: link Roblox creator access for game pass management.")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @guild_owner_only()
    async def linkasowner(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            raise ConfigurationError("This command can only be used inside a server.")
        if not self.bot.settings.roblox_owner_oauth_enabled:
            raise ConfigurationError(
                "Roblox owner OAuth is not configured yet. Set ROBLOX_OWNER_CLIENT_ID, "
                "ROBLOX_OWNER_CLIENT_SECRET, and ROBLOX_OWNER_REDIRECT_URI first."
            )

        await defer_interaction_response(interaction, ephemeral=True)
        state = await self.bot.services.roblox_creator.create_state(interaction.guild.id, interaction.user.id)
        authorization_url = self.bot.services.roblox_creator.build_authorization_url(state)

        embed = discord.Embed(title="Link Roblox Owner Access", color=discord.Color.blurple())
        embed.description = (
            "Authorize the bot to manage Roblox game passes for this server. "
            "This owner-only connection is separate from the normal `/link` user flow."
        )
        if self.bot.settings.roblox_owner_universe_id is not None:
            embed.add_field(
                name="Configured Universe",
                value=str(self.bot.settings.roblox_owner_universe_id),
                inline=False,
            )
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="Authorize Roblox Owner Access",
                style=discord.ButtonStyle.link,
                url=authorization_url,
            )
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="ownergamepasses", description="Server owner: view Roblox game passes and their linked systems.")
    @app_commands.describe(search="Optional name or ID filter for the game pass list.")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @guild_owner_only()
    async def ownergamepasses(self, interaction: discord.Interaction, search: str | None = None) -> None:
        if interaction.guild is None:
            raise ConfigurationError("This command can only be used inside a server.")

        await defer_interaction_response(interaction, ephemeral=True)
        gamepasses = await self.bot.services.roblox_creator.list_gamepasses(
            self.bot,
            interaction.guild.id,
            interaction.user.id,
        )
        if search:
            normalized = search.casefold().strip()
            gamepasses = [
                gamepass
                for gamepass in gamepasses
                if normalized in gamepass.name.casefold() or normalized in str(gamepass.game_pass_id)
            ]

        if not gamepasses:
            await interaction.followup.send("No Roblox game passes matched that filter.", ephemeral=True)
            return

        lines: list[str] = []
        for gamepass in gamepasses[:15]:
            linked_system = await _linked_system_for_gamepass(self.bot, gamepass.game_pass_id)
            system_label = linked_system.name if linked_system is not None else "not linked"
            sale_label = "on sale" if gamepass.is_for_sale else "off sale"
            lines.append(
                f"• **{gamepass.name}** | `{gamepass.game_pass_id}` | {_gamepass_price_label(gamepass)} | {sale_label} | system: {system_label}"
            )

        embed = discord.Embed(title="Roblox Game Passes", color=discord.Color.blurple())
        embed.description = "\n".join(lines)
        embed.set_footer(
            text=(
                f"Showing {min(len(gamepasses), 15)} of {len(gamepasses)} game passes. "
                "Use /connectgamepass and /sendgamepass for the next steps."
            )
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="creategamepass", description="Server owner: create a Roblox game pass for the configured universe.")
    @app_commands.describe(
        name="The name of the new game pass.",
        price="Default Robux price.",
        description="Optional game pass description.",
        system="Optional system to connect immediately after creation.",
        for_sale="Whether the game pass should be sold immediately.",
        regional_pricing="Whether Roblox regional pricing should be enabled.",
        image="Optional thumbnail image.",
    )
    @app_commands.autocomplete(system=owner_system_autocomplete)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @guild_owner_only()
    async def creategamepass(
        self,
        interaction: discord.Interaction,
        name: str,
        price: app_commands.Range[int, 1, 1000000000],
        description: str | None = None,
        system: str | None = None,
        for_sale: bool = True,
        regional_pricing: bool = True,
        image: discord.Attachment | None = None,
    ) -> None:
        if interaction.guild is None:
            raise ConfigurationError("This command can only be used inside a server.")
        if image is not None and image.content_type and not image.content_type.startswith("image/"):
            await interaction.response.send_message("The thumbnail must be an image attachment.", ephemeral=True)
            return

        await defer_interaction_response(interaction, ephemeral=True)
        image_upload: tuple[str, bytes, str | None] | None = None
        if image is not None:
            image_upload = (image.filename, await image.read(), image.content_type)

        gamepass = await self.bot.services.roblox_creator.create_gamepass(
            self.bot,
            interaction.guild.id,
            interaction.user.id,
            name=name,
            description=description,
            price=price,
            is_for_sale=for_sale,
            is_regional_pricing_enabled=regional_pricing,
            image_upload=image_upload,
        )

        linked_system: SystemRecord | None = None
        if system:
            selected_system = await self.bot.services.systems.get_system_by_name(system)
            linked_system = await self.bot.services.systems.set_system_gamepass(selected_system.id, str(gamepass.game_pass_id))

        embed = _build_gamepass_embed(gamepass, linked_system)
        embed.title = f"Created Game Pass: {gamepass.name}"
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="configuregamepass", description="Server owner: update a Roblox game pass configuration.")
    @app_commands.describe(
        gamepass="The Roblox game pass to update.",
        name="Optional new name.",
        description="Optional new description.",
        price="Optional new Robux price.",
        for_sale="Optional sale status.",
        regional_pricing="Optional regional pricing setting.",
        image="Optional replacement thumbnail image.",
    )
    @app_commands.autocomplete(gamepass=owner_gamepass_autocomplete)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @guild_owner_only()
    async def configuregamepass(
        self,
        interaction: discord.Interaction,
        gamepass: str,
        name: str | None = None,
        description: str | None = None,
        price: app_commands.Range[int, 1, 1000000000] | None = None,
        for_sale: bool | None = None,
        regional_pricing: bool | None = None,
        image: discord.Attachment | None = None,
    ) -> None:
        if interaction.guild is None:
            raise ConfigurationError("This command can only be used inside a server.")
        if image is not None and image.content_type and not image.content_type.startswith("image/"):
            await interaction.response.send_message("The thumbnail must be an image attachment.", ephemeral=True)
            return
        if all(value is None for value in (name, description, price, for_sale, regional_pricing, image)):
            await interaction.response.send_message("Provide at least one field to update.", ephemeral=True)
            return

        await defer_interaction_response(interaction, ephemeral=True)
        image_upload: tuple[str, bytes, str | None] | None = None
        if image is not None:
            image_upload = (image.filename, await image.read(), image.content_type)

        updated_gamepass = await self.bot.services.roblox_creator.update_gamepass(
            self.bot,
            interaction.guild.id,
            interaction.user.id,
            game_pass_id=int(gamepass),
            name=name,
            description=description,
            price=price,
            is_for_sale=for_sale,
            is_regional_pricing_enabled=regional_pricing,
            image_upload=image_upload,
        )
        linked_system = await _linked_system_for_gamepass(self.bot, updated_gamepass.game_pass_id)
        embed = _build_gamepass_embed(updated_gamepass, linked_system)
        embed.title = f"Updated Game Pass: {updated_gamepass.name}"
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="connectgamepass", description="Server owner: connect a Roblox game pass to a system.")
    @app_commands.describe(gamepass="The Roblox game pass to connect.", system="The system that should use this game pass.")
    @app_commands.autocomplete(gamepass=owner_gamepass_autocomplete, system=owner_system_autocomplete)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @guild_owner_only()
    async def connectgamepass(self, interaction: discord.Interaction, gamepass: str, system: str) -> None:
        if interaction.guild is None:
            raise ConfigurationError("This command can only be used inside a server.")

        await defer_interaction_response(interaction, ephemeral=True)
        gamepass_record = await self.bot.services.roblox_creator.get_gamepass(
            self.bot,
            interaction.guild.id,
            interaction.user.id,
            int(gamepass),
        )
        system_record = await self.bot.services.systems.get_system_by_name(system)
        linked_system = await self.bot.services.systems.set_system_gamepass(system_record.id, str(gamepass_record.game_pass_id))

        embed = _build_gamepass_embed(gamepass_record, linked_system)
        embed.title = f"Connected {gamepass_record.name}"
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="sendgamepass", description="Server owner: post a Roblox game pass buy link into a channel.")
    @app_commands.describe(gamepass="The Roblox game pass to post.", channel="The channel that should receive the buy button.")
    @app_commands.autocomplete(gamepass=owner_gamepass_autocomplete)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @guild_owner_only()
    async def sendgamepass(
        self,
        interaction: discord.Interaction,
        gamepass: str,
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild is None:
            raise ConfigurationError("This command can only be used inside a server.")

        await defer_interaction_response(interaction, ephemeral=True)
        gamepass_record = await self.bot.services.roblox_creator.get_gamepass(
            self.bot,
            interaction.guild.id,
            interaction.user.id,
            int(gamepass),
        )
        if not gamepass_record.is_for_sale:
            raise ExternalServiceError("That game pass is not currently for sale. Use /configuregamepass to enable sales first.")

        linked_system = await _linked_system_for_gamepass(self.bot, gamepass_record.game_pass_id)
        if linked_system is None:
            raise NotFoundError(
                "That game pass is not connected to a system yet. Use /connectgamepass before posting the buy link."
            )

        gamepass_url = self.bot.services.roblox_creator.gamepass_url(gamepass_record.game_pass_id)
        embed = _build_gamepass_embed(gamepass_record, linked_system)
        embed.title = f"Buy {linked_system.name}"
        embed.description = (
            f"Buy **{linked_system.name}** through this Roblox game pass.\n\n"
            f"Price: **{_gamepass_price_label(gamepass_record)}**"
        )
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="Buy Game Pass",
                style=discord.ButtonStyle.link,
                url=gamepass_url,
            )
        )

        try:
            await channel.send(embed=embed, view=view)
        except discord.Forbidden as exc:
            raise ExternalServiceError("I do not have permission to send messages in that channel.") from exc
        except discord.HTTPException as exc:
            raise ExternalServiceError("I could not send the game pass message to that channel.") from exc

        await interaction.followup.send(
            f"Posted **{gamepass_record.name}** to {channel.mention}.",
            ephemeral=True,
        )


async def setup(bot: SalesBot) -> None:
    await bot.add_cog(RobloxOwnerCog(bot))