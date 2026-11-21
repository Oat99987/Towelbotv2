import discord
from discord.ext import commands
from common import embed, staff_only, log_action, configured_id

class TicketPanel(discord.ui.View):
    def __init__(self,bot): super().__init__(timeout=None); self.bot=bot
    @discord.ui.button(label="Open support ticket",style=discord.ButtonStyle.primary,custom_id="nation:ticket:open")
    async def open_ticket(self,interaction,button):
        guild=interaction.guild
        overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False),
                    interaction.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)}
        staff_id=self.bot.config.get("roles",{}).get("staff")
        staff=guild.get_role(int(staff_id)) if staff_id else None
        if staff: overwrites[staff]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        category_id=configured_id(self.bot,"channels","ticket_category")
        category=guild.get_channel(category_id) if category_id else None
        channel=await guild.create_text_channel(f"ticket-{interaction.user.name}".lower()[:90],category=category,overwrites=overwrites)
        await channel.send(f"{interaction.user.mention} Support staff will be with you. Close this ticket when finished.")
        await interaction.response.send_message(f"Ticket created: {channel.mention}")

class Extras(commands.Cog):
    def __init__(self,bot): self.bot=bot

    @commands.hybrid_command(name="announce",description="Post an announcement embed.")
    @staff_only()
    async def announce(self,ctx,channel:discord.TextChannel,title,*,message):
        await channel.send(embed=embed(title,message,"info"))
        await log_action(self.bot,ctx.guild,ctx.author,"Announcement posted",channel,title)

    @commands.hybrid_command(name="ticket-panel",description="Post the support ticket panel.")
    @staff_only()
    async def ticket_panel(self,ctx):
        await ctx.send(embed=embed("Support","Open a private support ticket using the button below."),view=TicketPanel(self.bot))
        await log_action(self.bot,ctx.guild,ctx.author,"Ticket panel posted")

    @commands.hybrid_command(name="poll",description="Create a simple reaction poll.")
    @staff_only()
    async def poll(self,ctx,question,option_a,option_b):
        e=embed(question,f"🇦 {option_a}\n🇧 {option_b}")
        msg=await ctx.send(embed=e)
        await msg.add_reaction("🇦"); await msg.add_reaction("🇧")
        await log_action(self.bot,ctx.guild,ctx.author,"Poll created",details=question)

async def setup(bot): await bot.add_cog(Extras(bot))
