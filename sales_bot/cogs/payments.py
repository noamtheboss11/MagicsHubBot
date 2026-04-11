from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sales_bot.bot import SalesBot
from sales_bot.exceptions import AlreadyExistsError, PermissionDeniedError
from sales_bot.ui.common import PaginatedSelectView


class PaymentsCog(commands.Cog):
    def __init__(self, bot: SalesBot) -> None:
        self.bot = bot

    async def _validate_buyer(self, user_id: int) -> None:
        if await self.bot.services.blacklist.is_blacklisted(user_id):
            raise PermissionDeniedError("Blacklisted users cannot purchase or receive systems.")

    @app_commands.command(name="buywithpaypal", description="Choose a system with a PayPal link and receive the payment link.")
    async def buywithpaypal(self, interaction: discord.Interaction) -> None:
        await self._validate_buyer(interaction.user.id)

        systems = await self.bot.services.systems.list_paypal_enabled_systems()
        if not systems:
            await interaction.response.send_message("No systems currently have PayPal purchase links configured.", ephemeral=True)
            return

        async def on_selected(
            select_interaction: discord.Interaction,
            system: object,
            parent_view: PaginatedSelectView,
        ) -> None:
            selected_system = system
            if await self.bot.services.ownership.user_owns_system(interaction.user.id, selected_system.id):
                raise AlreadyExistsError("You already own that system.")

            purchase = await self.bot.services.payments.create_purchase(
                interaction.user.id,
                selected_system.id,
                selected_system.paypal_link,
            )

            embed = discord.Embed(title=f"Pay for {selected_system.name}", color=discord.Color.green())
            embed.description = "Use the PayPal button below. Once the webhook reports the payment as completed, the bot will DM your system automatically."
            embed.add_field(name="Purchase ID", value=str(purchase.id), inline=False)
            embed.add_field(name="Webhook Endpoint", value=f"{self.bot.settings.public_base_url}/webhooks/paypal/simulate", inline=False)
            link_view = discord.ui.View()
            link_view.add_item(
                discord.ui.Button(
                    label="Open PayPal",
                    style=discord.ButtonStyle.link,
                    url=selected_system.paypal_link,
                )
            )
            await select_interaction.response.edit_message(embed=embed, view=link_view)

        view = PaginatedSelectView(
            actor_id=interaction.user.id,
            items=systems,
            placeholder="Select a system to buy",
            option_builder=lambda system: discord.SelectOption(
                label=system.name[:100],
                description=(system.description or "Configured PayPal checkout")[:100],
                value=str(system.id),
            ),
            value_getter=lambda system: str(system.id),
            on_selected=on_selected,
        )
        await interaction.response.send_message(
            "Pick a system to purchase via PayPal.",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="buywithrobux", description="Choose a system with a Roblox gamepass and open its Robux purchase link.")
    async def buywithrobux(self, interaction: discord.Interaction) -> None:
        await self._validate_buyer(interaction.user.id)

        systems = await self.bot.services.systems.list_robux_enabled_systems()
        if not systems:
            await interaction.response.send_message("No systems currently have Roblox gamepass links configured.", ephemeral=True)
            return

        async def on_selected(
            select_interaction: discord.Interaction,
            system: object,
            parent_view: PaginatedSelectView,
        ) -> None:
            selected_system = system
            if await self.bot.services.ownership.user_owns_system(interaction.user.id, selected_system.id):
                raise AlreadyExistsError("You already own that system.")

            gamepass_url = self.bot.services.systems.gamepass_url_for_id(selected_system.roblox_gamepass_id)
            embed = discord.Embed(title=f"Buy {selected_system.name} with Robux", color=discord.Color.blurple())
            embed.description = "Use the Roblox gamepass button below to complete the Robux purchase for this system."
            embed.add_field(name="Gamepass", value=gamepass_url or "Not configured", inline=False)

            link_view = discord.ui.View()
            link_view.add_item(
                discord.ui.Button(
                    label="Open Gamepass",
                    style=discord.ButtonStyle.link,
                    url=gamepass_url,
                )
            )
            await select_interaction.response.edit_message(embed=embed, view=link_view)

        view = PaginatedSelectView(
            actor_id=interaction.user.id,
            items=systems,
            placeholder="Select a system to buy with Robux",
            option_builder=lambda system: discord.SelectOption(
                label=system.name[:100],
                description=(system.description or "Configured Roblox gamepass")[:100],
                value=str(system.id),
            ),
            value_getter=lambda system: str(system.id),
            on_selected=on_selected,
        )
        await interaction.response.send_message(
            "Pick a system to purchase via Robux.",
            view=view,
            ephemeral=True,
        )


async def setup(bot: SalesBot) -> None:
    await bot.add_cog(PaymentsCog(bot))
