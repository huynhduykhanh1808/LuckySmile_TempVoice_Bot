import os
import sqlite3
import logging

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "tempvoice.db")

TARGET_CATEGORY_ID = 1304790158763098224
GENERATOR_NAME = "➕・Tạo Phòng"
ROOM_PREFIX = "🔊"

if not TOKEN:
    raise RuntimeError("Chưa có DISCORD_TOKEN trong biến môi trường!")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("TempVoice")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True

class VoiceBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        log.info("Đã đồng bộ thành công cả 6 lệnh Slash!")

bot = VoiceBot()

# Database Helper Functions
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            guild_id INTEGER NOT NULL,
            channel_id INTEGER PRIMARY KEY,
            owner_id INTEGER NOT NULL
        )
    """)
    con.commit()
    con.close()

def save_room(guild_id: int, channel_id: int, owner_id: int):
    con = db()
    con.execute("INSERT OR REPLACE INTO rooms(guild_id, channel_id, owner_id) VALUES (?, ?, ?)", 
                (guild_id, channel_id, owner_id))
    con.commit()
    con.close()

def get_owned_room(guild_id: int, owner_id: int):
    con = db()
    row = con.execute("SELECT * FROM rooms WHERE guild_id=? AND owner_id=?", (guild_id, owner_id)).fetchone()
    con.close()
    return row

def get_room_by_channel(channel_id: int):
    con = db()
    row = con.execute("SELECT * FROM rooms WHERE channel_id=?", (channel_id,)).fetchone()
    con.close()
    return row

def delete_room_record(channel_id: int):
    con = db()
    con.execute("DELETE FROM rooms WHERE channel_id=?", (channel_id,))
    con.commit()
    con.close()

def get_user_voice_channel(interaction: discord.Interaction):
    if interaction.user.voice and isinstance(interaction.user.voice.channel, discord.VoiceChannel):
        return interaction.user.voice.channel
    return None

@bot.event
async def on_ready():
    init_db()
    log.info(f"Bot đã sẵn sàng: {bot.user}")

# ==========================================
# DANH SÁCH 6 LỆNH SLASH ĐỘC LẬP
# ==========================================

# Lệnh 1: /setup
@bot.tree.command(name="setup", description="Khởi tạo danh mục và phòng tạo Voice")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    guild = interaction.guild
    category = guild.get_channel(TARGET_CATEGORY_ID)
    
    if not category or not isinstance(category, discord.CategoryChannel):
        category = await guild.create_category("🔊 PHÒNG THOẠI TAM")

    gen_channel = discord.utils.get(category.voice_channels, name=GENERATOR_NAME)
    if not gen_channel:
        await category.create_voice_channel(GENERATOR_NAME)

    await interaction.response.send_message("✅ Đã thiết lập hệ thống Tạo Phòng!", ephemeral=True)

# Lệnh 2: /name
@bot.tree.command(name="name", description="Đổi tên phòng thoại của bạn")
@app_commands.describe(ten_moi="Tên mới cho phòng thoại")
async def name_cmd(interaction: discord.Interaction, ten_moi: str):
    channel = get_user_voice_channel(interaction)
    if not channel:
        return await interaction.response.send_message("❌ Bạn phải ở trong phòng thoại của mình!", ephemeral=True)
    
    room = get_room_by_channel(channel.id)
    if not room or room["owner_id"] != interaction.user.id:
        return await interaction.response.send_message("❌ Bạn không phải chủ sở hữu phòng này!", ephemeral=True)

    await channel.edit(name=f"{ROOM_PREFIX} {ten_moi}")
    await interaction.response.send_message(f"✅ Đã đổi tên phòng thành: **{ten_moi}**", ephemeral=True)

# Lệnh 3: /limit
@bot.tree.command(name="limit", description="Giới hạn số người trong phòng")
@app_commands.describe(so_luong="Số lượng người tối đa (0 = không giới hạn)")
async def limit_cmd(interaction: discord.Interaction, so_luong: int):
    channel = get_user_voice_channel(interaction)
    if not channel:
        return await interaction.response.send_message("❌ Bạn phải ở trong phòng thoại của mình!", ephemeral=True)
    
    room = get_room_by_channel(channel.id)
    if not room or room["owner_id"] != interaction.user.id:
        return await interaction.response.send_message("❌ Bạn không phải chủ sở hữu phòng này!", ephemeral=True)

    limit = max(0, min(99, so_luong))
    await channel.edit(user_limit=limit)
    msg = "không giới hạn" if limit == 0 else f"{limit} người"
    await interaction.response.send_message(f"✅ Đã đặt giới hạn phòng: **{msg}**", ephemeral=True)

# Lệnh 4: /lock
@bot.tree.command(name="lock", description="Khóa phòng thoại không cho người ngoài vào")
async def lock_cmd(interaction: discord.Interaction):
    channel = get_user_voice_channel(interaction)
    if not channel:
        return await interaction.response.send_message("❌ Bạn phải ở trong phòng thoại của mình!", ephemeral=True)
    
    room = get_room_by_channel(channel.id)
    if not room or room["owner_id"] != interaction.user.id:
        return await interaction.response.send_message("❌ Bạn không phải chủ sở hữu phòng này!", ephemeral=True)

    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.connect = False
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message("🔒 Đã khóa phòng thành công!", ephemeral=True)

# Lệnh 5: /unlock
@bot.tree.command(name="unlock", description="Mở khóa phòng thoại")
async def unlock_cmd(interaction: discord.Interaction):
    channel = get_user_voice_channel(interaction)
    if not channel:
        return await interaction.response.send_message("❌ Bạn phải ở trong phòng thoại của mình!", ephemeral=True)
    
    room = get_room_by_channel(channel.id)
    if not room or room["owner_id"] != interaction.user.id:
        return await interaction.response.send_message("❌ Bạn không phải chủ sở hữu phòng này!", ephemeral=True)

    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.connect = True
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message("🔓 Đã mở khóa phòng!", ephemeral=True)

# Lệnh 6: /claim
@bot.tree.command(name="claim", description="Nhận chủ sở hữu phòng nếu chủ cũ đã rời đi")
async def claim_cmd(interaction: discord.Interaction):
    channel = get_user_voice_channel(interaction)
    if not channel:
        return await interaction.response.send_message("❌ Bạn phải ở trong phòng thoại cần nhận chủ!", ephemeral=True)
    
    room = get_room_by_channel(channel.id)
    if not room:
        return await interaction.response.send_message("❌ Kênh thoại này không phải phòng tự động!", ephemeral=True)

    owner = interaction.guild.get_member(room["owner_id"])
    if owner and owner in channel.members:
        return await interaction.response.send_message("❌ Chủ sở hữu hiện tại vẫn đang ở trong phòng!", ephemeral=True)

    save_room(interaction.guild.id, channel.id, interaction.user.id)
    await interaction.response.send_message(f"👑 **{interaction.user.display_name}** đã trở thành chủ phòng mới!", ephemeral=True)

# ==========================================
# EVENT XỬ LÝ TẠO PHÒNG VÀ TỰ DỌN DẸP
# ==========================================
@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if after.channel and isinstance(after.channel, discord.VoiceChannel):
        if after.channel.category_id == TARGET_CATEGORY_ID and after.channel.name == GENERATOR_NAME:
            guild = member.guild
            category = after.channel.category

            existing = get_owned_room(guild.id, member.id)
            if existing:
                old_channel = guild.get_channel(existing["channel_id"])
                if isinstance(old_channel, discord.VoiceChannel):
                    try:
                        await member.move_to(old_channel)
                        return
                    except discord.HTTPException:
                        pass
                else:
                    delete_room_record(existing["channel_id"])

            new_channel = await guild.create_voice_channel(
                name=f"{ROOM_PREFIX} Phòng của {member.display_name}",
                category=category
            )
            await member.move_to(new_channel)
            save_room(guild.id, new_channel.id, member.id)

    if before.channel and isinstance(before.channel, discord.VoiceChannel):
        if before.channel.category_id == TARGET_CATEGORY_ID and before.channel.name != GENERATOR_NAME:
            con = db()
            row = con.execute("SELECT * FROM rooms WHERE channel_id=?", (before.channel.id,)).fetchone()
            con.close()

            if row and len(before.channel.members) == 0:
                delete_room_record(before.channel.id)
                try:
                    await before.channel.delete(reason="Phòng trống")
                except discord.HTTPException:
                    pass

if __name__ == "__main__":
    init_db()
    bot.run(TOKEN)