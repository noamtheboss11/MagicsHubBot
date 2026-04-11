from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sales_bot.bot import SalesBot
from sales_bot.exceptions import ConfigurationError


class OAuthCog(commands.Cog):
    def __init__(self, bot: SalesBot) -> None:
        self.bot = bot

    @app_commands.command(name="link", description="קבלת קישור לקישור חשבון הרובלוקס שלך.")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def link(self, interaction: discord.Interaction) -> None:
        if not self.bot.settings.roblox_oauth_enabled:
            raise ConfigurationError(
                "מערכת קישור הרובלוקס עדיין לא מוגדרת. יש להגדיר קודם את משתני הסביבה של Roblox OAuth."
            )

        state = await self.bot.services.oauth.create_state(interaction.user.id)
        authorization_url = self.bot.services.oauth.build_authorization_url(state)

        embed = discord.Embed(title="קישור חשבון Roblox", color=discord.Color.blurple())
        embed.description = "לחץ על הכפתור למטה כדי לקשר את חשבון הרובלוקס שלך. אחרי האישור, הבוט ישמור את החשבון המקושר שלך."

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="קשר Roblox", style=discord.ButtonStyle.link, url=authorization_url))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=interaction.guild is not None)


async def setup(bot: SalesBot) -> None:
    await bot.add_cog(OAuthCog(bot))
