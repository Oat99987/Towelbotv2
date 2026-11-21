import discord
from discord.ext import commands
from common import embed, admin, configured_role, audit

class VoteView(discord.ui.View):
    def __init__(self,cog,election_id,candidates):
        super().__init__(timeout=None); self.cog=cog; self.election_id=election_id
        for cid,name in candidates:
            button=discord.ui.Button(label=name[:80],style=discord.ButtonStyle.primary,custom_id=f"nation:vote:{election_id}:{cid}")
            button.callback=self.callback_for(cid); self.add_item(button)
    def callback_for(self,candidate_id):
        async def callback(interaction):
            citizen=await configured_role(interaction.guild,self.cog.bot,"citizen")
            if not citizen or citizen not in interaction.user.roles:
                return await interaction.response.send_message("Only citizens may vote.")
            election=await self.cog.bot.db.fetchone("SELECT status FROM elections WHERE id=?",(self.election_id,))
            if not election or election["status"]!="open": return await interaction.response.send_message("Voting is closed.")
            try:
                await self.cog.bot.db.execute("INSERT INTO election_votes(election_id,voter_id,candidate_id) VALUES(?,?,?)",(self.election_id,interaction.user.id,candidate_id))
            except Exception:
                return await interaction.response.send_message("Your vote has already been recorded.")
            await interaction.response.send_message("Vote recorded. Your choice is not displayed publicly.")
        return callback

class Elections(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @commands.hybrid_command(name="election-create",description="Create an election.")
    @admin()
    async def create(self,ctx,*,title):
        cur=await self.bot.db.execute("INSERT INTO elections(guild_id,title) VALUES(?,?)",(ctx.guild.id,title))
        await audit(self.bot,ctx.guild,ctx.author,"Election created",details=f"#{cur.lastrowid} {title}")
        await ctx.send(embed=embed("Election created",f"ID `{cur.lastrowid}`. Add candidates next.","success"))
    @commands.hybrid_command(name="candidate-add",description="Add a candidate.")
    @admin()
    async def candidate(self,ctx,election_id:int,*,name):
        e=await self.bot.db.fetchone("SELECT id FROM elections WHERE id=? AND guild_id=?",(election_id,ctx.guild.id))
        if not e: return await ctx.send(embed=embed("Command error","Election not found.","error"))
        await self.bot.db.execute("INSERT INTO candidates(election_id,name) VALUES(?,?)",(election_id,name))
        await audit(self.bot,ctx.guild,ctx.author,"Election candidate added",details=f"{election_id}: {name}")
        await ctx.send(embed=embed("Candidate added",name,"success"))
    @commands.hybrid_command(name="election-open",description="Open an election.")
    @admin()
    async def open(self,ctx,election_id:int):
        e=await self.bot.db.fetchone("SELECT title,status FROM elections WHERE id=? AND guild_id=?",(election_id,ctx.guild.id))
        rows=await self.bot.db.fetchall("SELECT id,name FROM candidates WHERE election_id=?",(election_id,))
        if not e or len(rows)<2: return await ctx.send(embed=embed("Command error","Election needs at least two candidates.","error"))
        if e["status"]=="open": return await ctx.send(embed=embed("Command error","Already open.","error"))
        await self.bot.db.execute("UPDATE elections SET status='open',channel_id=? WHERE id=?",(ctx.channel.id,election_id))
        view=VoteView(self,election_id,[(r["id"],r["name"]) for r in rows])
        msg=await ctx.send(embed=embed(e["title"],"Citizens: select one candidate. Public display contains no voter identities."),view=view)
        await self.bot.db.execute("UPDATE elections SET message_id=? WHERE id=?",(msg.id,election_id))
        await audit(self.bot,ctx.guild,ctx.author,"Election opened",details=str(election_id))
    @commands.hybrid_command(name="election-results",description="View aggregate election results.")
    @admin()
    async def results(self,ctx,election_id:int):
        rows=await self.bot.db.fetchall("""SELECT c.name,COUNT(v.voter_id) votes FROM candidates c
        LEFT JOIN election_votes v ON v.candidate_id=c.id WHERE c.election_id=? GROUP BY c.id ORDER BY votes DESC""",(election_id,))
        total=sum(r["votes"] for r in rows)
        desc="\n".join(f"**{r['name']}** · {r['votes']} votes · {('█'*int(12*r['votes']/total)) if total else '░'}" for r in rows) or "No candidates."
        await ctx.send(embed=embed("Election results",desc))
    @commands.hybrid_command(name="election-close",description="Close election and post results.")
    @admin()
    async def close(self,ctx,election_id:int):
        await self.bot.db.execute("UPDATE elections SET status='closed' WHERE id=? AND guild_id=?",(election_id,ctx.guild.id))
        rows=await self.bot.db.fetchall("""SELECT c.name,COUNT(v.voter_id) votes FROM candidates c
        LEFT JOIN election_votes v ON v.candidate_id=c.id WHERE c.election_id=? GROUP BY c.id ORDER BY votes DESC""",(election_id,))
        total=sum(r["votes"] for r in rows)
        desc="\n".join(f"**{r['name']}** — {r['votes']} votes · {('█'*int(12*r['votes']/total)) if total else '░'}" for r in rows) or "No results."
        await ctx.send(embed=embed("Election closed — results",desc,"success"))
        await audit(self.bot,ctx.guild,ctx.author,"Election closed",details=str(election_id))
async def setup(bot): await bot.add_cog(Elections(bot))
