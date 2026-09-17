import os
import sqlite3
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "tempvoice.db")
DEFAULT_CATEGORY = os.getenv("DEFAULT_CATEGORY", "🎙️・Lucky Smile Rooms")
DEFAULT_GENERATOR = os.getenv("DEFAULT_GENERATOR", "➕・Tạo Phòng")
DEFAULT_CONTROL = os.getenv("DEFAULT_CONTROL", "🎛️・quản-lý-phòng")
ROOM_PREFIX = os.getenv("ROOM_PREFIX", "🔊")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("TempVoiceBot")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# -----------------------------
# DATABASE SQLITE
# -----------------------------
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS generators (
                guild_id INTEGER PRIMARY KEY,
                category_id INTEGER NOT NULL,
                generator_id INTEGER NOT NULL,
                control_channel_id INTEGER
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS rooms (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL
            )
        """)

def get_generator(guild_id: int):
    with db() as con:
        return con.execute("SELECT * FROM generators WHERE guild_id = ?", (guild_id,)).fetchone()

def save_generator(guild_id: int, category_id: int, generator_id: int, control_id: int):
    with db() as con:
        con.execute("""
            INSERT INTO generators(guild_id, category_id, generator_id, control_channel_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                category_id=excluded.category_id,
                generator_id=excluded.generator_id,
                control_channel_id=excluded.control_channel_id
        """, (guild_id, category_id, generator_id, control_id))

def save_room(guild_id: int, channel_id: int, owner_id: int, category_id: int):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO rooms VALUES (?, ?, ?, ?)", (guild_id, channel_id, owner_id, category_id))

def get_room(channel_id: int):
    with db() as con:
        return con.execute("SELECT * FROM rooms WHERE channel_id = ?", (channel_id,)).fetchone()

def delete_room_record(channel_id: int):
    with db() as con:
        con.execute("DELETE FROM rooms WHERE channel_id=?", (channel_id,))

# -----------------------------
# MODAL & SELECT UI
# -----------------------------
class LimitModal(discord.ui.Modal, title="Cài đặt giới hạn người dùng"):
    limit = discord.ui.TextInput(label="Số người tối đa (0 = Không giới hạn)", placeholder="0 - 99", min_length=1, max_length=2)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải ở trong phòng thoại của mình!", ephemeral=True)
        try:
            val = int(self.limit.value)
            if not (0 <= val <= 99): raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ Nhập số hợp lệ từ 0 đến 99!", ephemeral=True)

        await interaction.user.voice.channel.edit(user_limit=val)
        await interaction.response.send_message(f"✅ Đã đổi giới hạn phòng thành **{val}** người!", ephemeral=True)

class RenameModal(discord.ui.Modal, title="Đổi tên phòng thoại"):
    new_name = discord.ui.TextInput(label="Tên phòng mới", placeholder="Nhập tên phòng...", min_length=1, max_length=100)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải ở trong phòng thoại!", ephemeral=True)
        
        ch = interaction.user.voice.channel
        await ch.edit(name=f"{ROOM_PREFIX} {self.new_name.value}")
        await interaction.response.send_message(f"✅ Đã đổi tên phòng thành: **{self.new_name.value}**", ephemeral=True)

class RegionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Tự động", value="auto"),
            discord.SelectOption(label="Singapore", value="singapore"),
            discord.SelectOption(label="Hong Kong", value="hongkong"),
            discord.SelectOption(label="Japan", value="japan"),
            discord.SelectOption(label="US Central", value="us-central"),
            discord.SelectOption(label="Rotterdam", value="rotterdam")
        ]
        super().__init__(placeholder="🌐 Chọn khu vực máy chủ...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải ở trong phòng thoại!", ephemeral=True)
        
        region_val = None if self.values[0] == "auto" else self.values[0]
        await interaction.user.voice.channel.edit(rtc_region=region_val)
        await interaction.response.send_message(f"🪪 Đã chuyển khu vực máy chủ sang: **{self.values[0].upper()}**", ephemeral=True)

class RegionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(RegionSelect())

# -----------------------------
# BẢNG ĐIỀU KHIỂN CHÍNH (PANEL)
# -----------------------------
class VoiceControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Khóa", style=discord.ButtonStyle.secondary, emoji="🔒", row=0, custom_id="vc_lock")
    async def lock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải ở trong phòng!", ephemeral=True)
        ch = interaction.user.voice.channel
        await ch.set_permissions(interaction.guild.default_role, connect=False)
        await ch.set_permissions(interaction.user, connect=True)
        await interaction.response.send_message("🔒 Đã khóa phòng thoại!", ephemeral=True)

    @discord.ui.button(label="Mở khóa", style=discord.ButtonStyle.secondary, emoji="🔓", row=0, custom_id="vc_unlock")
    async def unlock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải ở trong phòng!", ephemeral=True)
        await interaction.user.voice.channel.set_permissions(interaction.guild.default_role, connect=None)
        await interaction.response.send_message("🔓 Đã mở khóa phòng thoại!", ephemeral=True)

    @discord.ui.button(label="Ẩn", style=discord.ButtonStyle.secondary, emoji="🥷", row=0, custom_id="vc_hide")
    async def hide_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải ở trong phòng!", ephemeral=True)
        await interaction.user.voice.channel.set_permissions(interaction.guild.default_role, view_channel=False)
        await interaction.response.send_message("🥷 Đã ẩn phòng thoại!", ephemeral=True)

    @discord.ui.button(label="Hiện", style=discord.ButtonStyle.secondary, emoji="👁️", row=0, custom_id="vc_unhide")
    async def unhide_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải ở trong phòng!", ephemeral=True)
        await interaction.user.voice.channel.set_permissions(interaction.guild.default_role, view_channel=None)
        await interaction.response.send_message("👁️ Đã hiện phòng thoại!", ephemeral=True)

    @discord.ui.button(label="Giới hạn", style=discord.ButtonStyle.secondary, emoji="👥", row=1, custom_id="vc_limit")
    async def limit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LimitModal())

    @discord.ui.button(label="Đổi tên", style=discord.ButtonStyle.secondary, emoji="✏️", row=1, custom_id="vc_rename")
    async def rename_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RenameModal())

    @discord.ui.button(label="Khu vực", style=discord.ButtonStyle.secondary, emoji="🪪", row=1, custom_id="vc_region")
    async def region_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🌐 Chọn khu vực máy chủ bên dưới:", view=RegionView(), ephemeral=True)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.danger, emoji="🖼️", row=1, custom_id="vc_reset")
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải ở trong phòng!", ephemeral=True)
        ch = interaction.user.voice.channel
        await ch.edit(name=f"{ROOM_PREFIX} Phòng của {interaction.user.display_name}", user_limit=0, rtc_region=None)
        await ch.set_permissions(interaction.guild.default_role, overwrite=None)
        await interaction.response.send_message("🖼️ Đã khôi phục cài đặt phòng!", ephemeral=True)

# -----------------------------
# LỆNH SLASH
# -----------------------------
@bot.tree.command(name="room-allow", description="Cho phép thành viên tham gia phòng")
async def room_allow(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải ở trong phòng tạm!", ephemeral=True)
    ch = interaction.user.voice.channel
    await ch.set_permissions(user, connect=True, view_channel=True)
    await interaction.response.send_message(f"✅ Đã cấp quyền cho {user.mention}!", ephemeral=True)

@bot.tree.command(name="room-deny", description="Cấm thành viên tham gia phòng")
async def room_deny(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải ở trong phòng tạm!", ephemeral=True)
    ch = interaction.user.voice.channel
    await ch.set_permissions(user, connect=False)
    if user.voice and user.voice.channel == ch:
        await user.move_to(None)
    await interaction.response.send_message(f"🚫 Đã cấm {user.mention}!", ephemeral=True)

@bot.tree.command(name="room-kick", description="Đuổi thành viên ra khỏi phòng")
async def room_kick(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải ở trong phòng tạm!", ephemeral=True)
    ch = interaction.user.voice.channel
    if user.voice and user.voice.channel == ch:
        await user.move_to(None)
        await interaction.response.send_message(f"👞 Đã đuổi {user.mention}!", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Thành viên không ở trong phòng!", ephemeral=True)

# -----------------------------
# SỰ KIỆN CHÍNH
# -----------------------------
@bot.event
async def on_ready():
    init_db()
    bot.add_view(VoiceControlView())
    await bot.tree.sync()
    log.info(f"Bot đã sẵn sàng với tên: {bot.user}")

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # 1. Tham gia Kênh Tạo Phòng
    if after.channel:
        row = get_generator(member.guild.id)
        if row and after.channel.id == row["generator_id"]:
            category = member.guild.get_channel(row["category_id"])
            new_channel = await member.guild.create_voice_channel(
                name=f"{ROOM_PREFIX} Phòng của {member.display_name}",
                category=category
            )
            await new_channel.set_permissions(member, manage_channels=True, move_members=True, connect=True)
            await member.move_to(new_channel)
            save_room(member.guild.id, new_channel.id, member.id, category.id)

            # Gửi Bảng Điều Khiển
            embed = discord.Embed(
                title="🎛️ BẢNG ĐIỀU KHIỂN PHÒNG THOẠI",
                description=f"Chủ phòng: {member.mention}\n\nNhấp vào các nút bên dưới để tùy chỉnh phòng!",
                color=discord.Color.purple()
            )
            await new_channel.send(content=f"👋 Chào mừng {member.mention}!", embed=embed, view=VoiceControlView())

    # 2. Xóa phòng khi trống
    if before.channel:
        room_data = get_room(before.channel.id)
        if room_data and len(before.channel.members) == 0:
            try:
                await before.channel.delete(reason="Phòng trống")
                delete_room_record(before.channel.id)
            except discord.HTTPException:
                pass

if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("Chưa cấu hình DISCORD_TOKEN!")
    bot.run(TOKEN)