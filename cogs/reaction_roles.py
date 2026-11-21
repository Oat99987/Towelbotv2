import discord
from discord.ext import commands
from common import embed, admin, audit

class RoleButton(discord.ui.Button):
    def __init__(self,role_id,label):
        super().__init__(label=label[:80],style=discord.ButtonStyle.secondary,custom_id=f"nation:rr:{role_id}")
        self.role_id=role_id
    async def callback(self,interaction):
        if interaction.guild is None:
            return await interaction.response.send_message("Use this role button inside the server.")
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Use this role button inside the server.")
        r=interaction.guild.get_role(self.role_id)
        if not r: return await interaction.response.send_message("That role no longer exists.")
        if r in interaction.user.roles:
            await interaction.user.remove_roles(r,reason="Self-assign role panel")
            await interaction.response.send_message(f"Removed {r.name}.")
        else:
            await interaction.user.add_roles(r,reason="Self-assign role panel")
            await interaction.response.send_message(f"Added {r.name}.")

class ReactionRoles(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @commands.hybrid_command(name="role-panel",description="Post a self-assign role button panel.")
    @admin()
    async def panel(self,ctx,*,role_list:str):
        # Format: Role ID=Label;Role ID=Label
        view=discord.ui.View(timeout=None)
        for part in role_list.split(";"):
            try: rid,label=part.split("=",1); r=ctx.guild.get_role(int(rid.strip()))
            except Exception: r=None
            if not r: return await ctx.send(embed=embed("Command error",f"Invalid role entry: `{part}`","error"))
            if r>=ctx.guild.me.top_role: return await ctx.send(embed=embed("Command error","Bot role must be above every self-assignable role.","error"))
            view.add_item(RoleButton(r.id,label.strip()))
        msg=await ctx.send(embed=embed("Choose your roles","Click a button to add or remove the corresponding role."),view=view)
        await audit(self.bot,ctx.guild,ctx.author,"Self-assign role panel posted",msg)
async def setup(bot): await bot.add_cog(ReactionRoles(bot))
