import os
import sqlite3
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "tempvoice.db")

# ID Danh mục cố định duy nhất
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

bot = commands.Bot(command_prefix="!", intents=intents)

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

def delete_room_record(channel_id: int):
    con = db()
    con.execute("DELETE FROM rooms WHERE channel_id=?", (channel_id,))
    con.commit()
    con.close()

@bot.event
async def on_ready():
    init_db()
    log.info(f"Bot đã sẵn sàng: {bot.user}")

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # 1. Khi thành viên tham gia vào kênh "➕・Tạo Phòng" thuộc danh mục quy định
    if after.channel and isinstance(after.channel, discord.VoiceChannel):
        if after.channel.category_id == TARGET_CATEGORY_ID and after.channel.name == GENERATOR_NAME:
            guild = member.guild
            category = after.channel.category

            # Kiểm tra xem thành viên này đã tạo phòng trước đó chưa
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

            # CHỈ TẠO ĐÚNG 1 PHÒNG THOẠI DUY NHẤT
            new_channel = await guild.create_voice_channel(
                name=f"{ROOM_PREFIX} Phòng của {member.display_name}",
                category=category
            )

            # Chuyển người dùng vào phòng vừa tạo
            await member.move_to(new_channel)
            save_room(guild.id, new_channel.id, member.id)

    # 2. Tự động dọn dẹp phòng trống khi không còn ai
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