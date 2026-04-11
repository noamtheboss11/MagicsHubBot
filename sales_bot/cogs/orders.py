from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sales_bot.bot import SalesBot
from sales_bot.checks import admin_only
from sales_bot.exceptions import ExternalServiceError
from sales_bot.ui.common import PaginatedSelectView
from sales_bot.ui.orders import OrderManagementView, OrderPanelView, build_order_record_embed


class OrdersCog(commands.Cog):
    def __init__(self, bot: SalesBot) -> None:
        self.bot = bot

    @app_commands.command(name="sendorderpanel", description="שליחת פאנל ההזמנות לערוץ ההזמנות.")
    @admin_only()
    async def sendorderpanel(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True)
            except discord.HTTPException as exc:
                if exc.code != 40060:
                    raise

        channel = self.bot.get_channel(self.bot.settings.order_channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(self.bot.settings.order_channel_id)

        if not isinstance(channel, discord.abc.Messageable):
            await interaction.followup.send("ערוץ ההזמנות לא תומך בשליחת הודעות.", ephemeral=True)
            return

        embed = discord.Embed(
            title="הזמנות בהכנה אישית",
            description="לחצו על הכפתור כאן למטה בכדי להזמין מערכת / משהו אחר ממגיק (המייסד) בהכנה אישית.",
            color=discord.Color.gold(),
        )
        view = OrderPanelView(self.bot)
        await channel.send(embed=embed, view=view)
        await interaction.followup.send(f"פאנל ההזמנות נשלח ל-<#{self.bot.settings.order_channel_id}>.", ephemeral=True)


async def setup(bot: SalesBot) -> None:
    await bot.add_cog(OrdersCog(bot))
    await bot.add_cog(OrderAdminCog(bot))


class OrderAdminCog(commands.GroupCog, group_name="orders", group_description="ניהול הזמנות"):
    def __init__(self, bot: SalesBot) -> None:
        self.bot = bot
        super().__init__()

    @app_commands.command(name="list", description="פתיחת רשימת ההזמנות הפעילות בתפריט בחירה.")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @admin_only()
    async def list_orders(self, interaction: discord.Interaction) -> None:
        async def send_acknowledgement(message: str) -> None:
            responder = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
            try:
                await responder(message, ephemeral=True)
            except discord.HTTPException as exc:
                if exc.code == 40060:
                    await interaction.followup.send(message, ephemeral=True)
                    return
                if exc.code == 10062:
                    return
                raise

        orders = await self.bot.services.orders.list_active_requests()
        if not orders:
            await send_acknowledgement("אין כרגע הזמנות פעילות.")
            return

        async def on_selected(
            select_interaction: discord.Interaction,
            order: object,
            parent_view: PaginatedSelectView,
        ) -> None:
            selected_order = order
            requester = None
            try:
                requester = await self.bot.fetch_user(selected_order.user_id)
            except discord.HTTPException:
                requester = None

            embed = build_order_record_embed("פרטי הזמנה פעילה", selected_order, user=requester)
            view = OrderManagementView(
                self.bot,
                actor_id=interaction.user.id,
                order=selected_order,
            )

            try:
                owner_dm = interaction.user.dm_channel or await interaction.user.create_dm()
                dm_message = await owner_dm.send(embed=embed, view=view)
            except discord.HTTPException as exc:
                raise ExternalServiceError("לא הצלחתי לשלוח את פרטי ההזמנה ל-DM שלך.") from exc

            view.message = dm_message
            await select_interaction.response.edit_message(
                content="ההזמנה נשלחה אליך ב-DM. אפשר לבחור הזמנה נוספת אם צריך.",
                view=parent_view,
            )

        view = PaginatedSelectView(
            actor_id=interaction.user.id,
            items=orders,
            placeholder="בחר הזמנה לפתיחה ב-DM",
            option_builder=lambda order: discord.SelectOption(
                label=f"Order #{order.id}"[:100],
                description=f"{order.requested_item[:70]} | User {order.user_id}"[:100],
                value=str(order.id),
            ),
            value_getter=lambda order: str(order.id),
            on_selected=on_selected,
        )
        try:
            owner_dm = interaction.user.dm_channel or await interaction.user.create_dm()
            await owner_dm.send(
                "בחר הזמנה מהרשימה כדי לפתוח אותה ב-DM שלך.",
                view=view,
            )
        except discord.HTTPException as exc:
            raise ExternalServiceError("לא הצלחתי לשלוח את רשימת ההזמנות ל-DM שלך.") from exc

        await send_acknowledgement(
            "בחר הזמנה מהרשימה כדי לפתוח אותה ב-DM שלך.",
        )