from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sales_bot.bot import SalesBot
from sales_bot.checks import admin_only


class EngagementCog(commands.Cog):
    def __init__(self, bot: SalesBot) -> None:
        self.bot = bot

    @app_commands.command(name="poll", description="Open the admin web panel for creating a poll.")
    @admin_only()
    async def poll(self, interaction: discord.Interaction) -> None:
        session = await self.bot.services.panels.create_session(
            admin_user_id=interaction.user.id,
            panel_type="poll-create",
        )
        panel_url = f"{self.bot.settings.public_base_url}/admin/polls/new?token={session.token}"
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Poll Panel", style=discord.ButtonStyle.link, url=panel_url))

        embed = discord.Embed(title="Poll Panel", color=discord.Color.blurple())
        embed.description = "Open the web panel below to build and publish a poll."
        embed.add_field(name="Link expires in", value=f"{self.bot.settings.admin_panel_session_minutes} minutes", inline=False)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="editpoll", description="Open the admin web panel for editing an existing poll.")
    @app_commands.describe(poll_id="The stored poll ID shown in the poll embed.")
    @admin_only()
    async def editpoll(self, interaction: discord.Interaction, poll_id: int) -> None:
        poll = await self.bot.services.polls.get_poll(poll_id)
        session = await self.bot.services.panels.create_session(
            admin_user_id=interaction.user.id,
            panel_type="poll-edit",
            target_id=poll.id,
        )
        panel_url = f"{self.bot.settings.public_base_url}/admin/polls/{poll.id}/edit?token={session.token}"
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Edit Poll", style=discord.ButtonStyle.link, url=panel_url))

        embed = discord.Embed(title=f"Edit Poll #{poll.id}", color=discord.Color.blurple())
        embed.description = poll.question
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="giveaway", description="Open the admin web panel for creating a giveaway.")
    @admin_only()
    async def giveaway(self, interaction: discord.Interaction) -> None:
        session = await self.bot.services.panels.create_session(
            admin_user_id=interaction.user.id,
            panel_type="giveaway-create",
        )
        panel_url = f"{self.bot.settings.public_base_url}/admin/giveaways/new?token={session.token}"
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Giveaway Panel", style=discord.ButtonStyle.link, url=panel_url))

        embed = discord.Embed(title="Giveaway Panel", color=discord.Color.green())
        embed.description = "Open the web panel below to build and publish a giveaway."
        embed.add_field(name="Link expires in", value=f"{self.bot.settings.admin_panel_session_minutes} minutes", inline=False)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="editgiveaway", description="Open the admin web panel for editing an existing giveaway.")
    @app_commands.describe(giveaway_id="The stored giveaway ID shown in the giveaway embed.")
    @admin_only()
    async def editgiveaway(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        giveaway = await self.bot.services.giveaways.get_giveaway(giveaway_id)
        session = await self.bot.services.panels.create_session(
            admin_user_id=interaction.user.id,
            panel_type="giveaway-edit",
            target_id=giveaway.id,
        )
        panel_url = f"{self.bot.settings.public_base_url}/admin/giveaways/{giveaway.id}/edit?token={session.token}"
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Edit Giveaway", style=discord.ButtonStyle.link, url=panel_url))

        embed = discord.Embed(title=f"Edit Giveaway #{giveaway.id}", color=discord.Color.green())
        embed.description = giveaway.title
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: SalesBot) -> None:
    await bot.add_cog(EngagementCog(bot))