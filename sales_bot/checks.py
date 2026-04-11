from __future__ import annotations

from typing import TYPE_CHECKING, cast

from discord import app_commands

if TYPE_CHECKING:
    import discord

    from sales_bot.bot import SalesBot


def admin_only() -> app_commands.Check:
    async def predicate(interaction: discord.Interaction) -> bool:
        bot = interaction.client
        if not hasattr(bot, "services"):
            raise app_commands.CheckFailure("Bot services are not ready yet. Try again in a moment.")

        sales_bot = cast("SalesBot", bot)

        if await sales_bot.services.admins.is_admin(interaction.user.id):
            return True

        raise app_commands.CheckFailure("Only bot admins can use this command.")

    return app_commands.check(predicate)
