import discord
from discord.ext import commands
from common import embed, admin, channel, role, audit

class TicketView(discord.ui.View):
    def __init__(self,bot): super().__init__(timeout=None); self.bot=bot
    @discord.ui.button(label="Open ticket",style=discord.ButtonStyle.primary,custom_id="nation:tickets:open")
    async def open(self,interaction,button):
        guild=interaction.guild
        existing=await self.bot.db.fetchone("SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND status='open'",(guild.id,interaction.user.id))
        if existing:
            ch=guild.get_channel(existing["channel_id"])
            return await interaction.response.send_message(f"You already have a ticket: {ch.mention if ch else 'channel unavailable'}")
        overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False),
                    interaction.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)}
        staff=role(guild,self.bot,"staff")
        if staff: overwrites[staff]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        category_id=self.bot.config.get("channels",{}).get("ticket_category")
        category=guild.get_channel(int(category_id)) if category_id else None
        ch=await guild.create_text_channel(f"ticket-{interaction.user.id}",category=category,overwrites=overwrites)
        cur=await self.bot.db.execute("INSERT INTO tickets(guild_id,channel_id,user_id) VALUES(?,?,?)",(guild.id,ch.id,interaction.user.id))
        await ch.send(embed=embed("Support ticket",f"{interaction.user.mention} Staff will assist you."),view=TicketCloseView(self.bot,cur.lastrowid))
        await interaction.response.send_message(f"Ticket opened: {ch.mention}")

class TicketCloseView(discord.ui.View):
    def __init__(self,bot,ticket_id): super().__init__(timeout=None); self.bot=bot; self.ticket_id=ticket_id
    @discord.ui.button(label="Close ticket",style=discord.ButtonStyle.danger,custom_id="nation:tickets:close")
    async def close(self,interaction,button):
        row=await self.bot.db.fetchone("SELECT * FROM tickets WHERE id=? AND status='open'",(self.ticket_id,))
        if not row: return await interaction.response.send_message("Ticket already closed.")
        staff=role(interaction.guild,self.bot,"staff")
        if interaction.user.id!=row["user_id"] and not interaction.user.guild_permissions.manage_channels and not (staff and staff in interaction.user.roles):
            return await interaction.response.send_message("Only the opener or staff can close this ticket.")
        await interaction.response.defer()
        lines=[]
        async for m in interaction.channel.history(limit=500,oldest_first=True):
            lines.append(f"[{m.created_at.isoformat()}] {m.author}: {m.content}")
        transcript="\n".join(lines)[-180000:]
        path=f"ticket-{self.ticket_id}-transcript.txt"
        import io
        file=discord.File(io.BytesIO(transcript.encode("utf-8")),filename=path)
        log=channel(interaction.guild,self.bot,"ticket_log")
        if log: await log.send(content=f"Transcript for ticket #{self.ticket_id}",file=file)
        await self.bot.db.execute("UPDATE tickets SET status='closed',closed_at=CURRENT_TIMESTAMP WHERE id=?",(self.ticket_id,))
        await audit(self.bot,interaction.guild,interaction.user,"Ticket closed",interaction.channel,str(self.ticket_id))
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        await interaction.followup.send("Ticket closed.")

class Tickets(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def cog_load(self): self.bot.add_view(TicketView(self.bot))
    @commands.hybrid_command(name="ticket-panel",description="Post the support ticket panel.")
    @admin()
    async def panel(self,ctx):
        await ctx.send(embed=embed("Support","Open a private ticket using the button."),view=TicketView(self.bot))
        await audit(self.bot,ctx.guild,ctx.author,"Ticket panel posted")
async def setup(bot): await bot.add_cog(Tickets(bot))
