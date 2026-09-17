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
DEFAULT_CATEGORY = os.getenv("CATEGORY_NAME", "🎙️・Lucky Smile Rooms")
DEFAULT_GENERATOR = os.getenv("GENERATOR_NAME", "➕・Tạo Phòng")
DEFAULT_CONTROL = os.getenv("CONTROL_CHANNEL_NAME", "🎛️・quản-lý-phòng")
ROOM_PREFIX = os.getenv("ROOM_PREFIX", "🔊")
DELETE_EMPTY = os.getenv("DELETE_EMPTY", "true").lower() == "true"

if not TOKEN:
    raise RuntimeError("Chưa có DISCORD_TOKEN trong file .env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("LuckySmileTempVoice")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True

# -----------------------------
# SQLite Database
# -----------------------------
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
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
    con.commit()
    con.close()

def get_generator(guild_id: int):
    con = db()
    row = con.execute("SELECT * FROM generators WHERE guild_id = ?", (guild_id,)).fetchone()
    con.close()
    return row

def save_generator(guild_id, category_id, generator_id, control_channel_id):
    con = db()
    con.execute("""
        INSERT INTO generators(guild_id, category_id, generator_id, control_channel_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            category_id=excluded.category_id,
            generator_id=excluded.generator_id,
            control_channel_id=excluded.control_channel_id
    """, (guild_id, category_id, generator_id, control_channel_id))
    con.commit()
    con.close()

def save_room(guild_id, channel_id, owner_id, category_id):
    con = db()
    con.execute("""
        INSERT OR REPLACE INTO rooms(guild_id, channel_id, owner_id, category_id)
        VALUES (?, ?, ?, ?)
    """, (guild_id, channel_id, owner_id, category_id))
    con.commit()
    con.close()

def get_room(channel_id: int):
    con = db()
    row = con.execute("SELECT * FROM rooms WHERE channel_id = ?", (channel_id,)).fetchone()
    con.close()
    return row

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

def all_rooms():
    con = db()
    rows = con.execute("SELECT * FROM rooms").fetchall()
    con.close()
    return rows

async def delete_temp_room(channel: discord.VoiceChannel):
    delete_room_record(channel.id)
    try:
        await channel.delete(reason="Lucky Smile TempVoice: Xóa phòng trống")
    except discord.HTTPException:
        pass

# -----------------------------
# Helpers & Modals
# -----------------------------
def room_for_member(member: discord.Member) -> Optional[discord.VoiceChannel]:
    if not member.voice or not isinstance(member.voice.channel, discord.VoiceChannel):
        return None
    ch = member.voice.channel
    return ch if get_room(ch.id) else None

class LimitModal(discord.ui.Modal, title="Cài đặt giới hạn người dùng"):
    limit = discord.ui.TextInput(
        label="Số lượng người tối đa (0 = Không giới hạn)",
        placeholder="Nhập số từ 0 đến 99...",
        min_length=1,
        max_length=2,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
        try:
            val = int(self.limit.value)
            if val < 0 or val > 99:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ Vui lòng nhập số hợp lệ từ 0 đến 99!", ephemeral=True)

        channel = interaction.user.voice.channel
        await channel.edit(user_limit=val)
        await interaction.response.send_message(f"✅ Đã đổi giới hạn phòng thành **{val}** người!", ephemeral=True)

class RenameModal(discord.ui.Modal, title="Đổi tên phòng thoại"):
    new_name = discord.ui.TextInput(
        label="Tên phòng mới",
        placeholder="Nhập tên phòng mới...",
        min_length=1,
        max_length=100,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)

        channel = interaction.user.voice.channel
        await channel.edit(name=f"{ROOM_PREFIX} {self.new_name.value}")
        await interaction.response.send_message(f"✅ Đã đổi tên phòng thành: **{self.new_name.value}**", ephemeral=True)

class TransferModal(discord.ui.Modal, title="Chuyển chủ phòng"):
    user_id = discord.ui.TextInput(
        label="ID Discord của người nhận",
        placeholder="Ví dụ: 123456789012345678",
        max_length=20
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = room_for_member(interaction.user)
        if not channel:
            return await interaction.response.send_message("❌ Bạn phải đang ở phòng tạm.", ephemeral=True)
        row = get_room(channel.id)
        if not row or row["owner_id"] != interaction.user.id:
            return await interaction.response.send_message("❌ Chỉ chủ phòng mới chuyển quyền được.", ephemeral=True)
        try:
            uid = int(str(self.user_id).strip())
        except ValueError:
            return await interaction.response.send_message("❌ ID không hợp lệ.", ephemeral=True)

        member = interaction.guild.get_member(uid)
        if not member:
            return await interaction.response.send_message("❌ Người này không ở server.", ephemeral=True)
        if not member.voice or member.voice.channel.id != channel.id:
            return await interaction.response.send_message("❌ Người nhận phải đang ở trong phòng.", ephemeral=True)

        save_room(interaction.guild.id, channel.id, member.id, channel.category_id if channel.category else 0)
        await interaction.response.send_message(f"👑 Đã chuyển chủ phòng cho {member.mention}.", ephemeral=True)

# -----------------------------
# Views & Selects
# -----------------------------
class RegionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Tự động (Automatic)", value="auto", description="Để Discord tự chọn khu vực"),
            discord.SelectOption(label="Singapore", value="singapore", description="Singapore"),
            discord.SelectOption(label="Hong Kong", value="hongkong", description="Hong Kong"),
            discord.SelectOption(label="Japan", value="japan", description="Nhật Bản"),
            discord.SelectOption(label="US Central", value="us-central", description="Trung Mỹ"),
            discord.SelectOption(label="US East", value="us-east", description="Đông Mỹ"),
            discord.SelectOption(label="US West", value="us-west", description="Tây Mỹ"),
            discord.SelectOption(label="Rotterdam", value="rotterdam", description="Châu Âu"),
        ]
        super().__init__(placeholder="🌐 Chọn khu vực máy chủ...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại!", ephemeral=True)
        
        channel = interaction.user.voice.channel
        region_val = None if self.values[0] == "auto" else self.values[0]
        try:
            await channel.edit(rtc_region=region_val)
            region_name = "Tự động" if self.values[0] == "auto" else self.values[0].upper()
            await interaction.response.send_message(f"🪪 Đã đổi khu vực máy chủ sang: **{region_name}**", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Không thể đổi khu vực: {e}", ephemeral=True)

class RegionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(RegionSelect())

class VoiceControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Khóa", style=discord.ButtonStyle.secondary, emoji="🔒", row=0, custom_id="vc_lock")
    async def lock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
        channel = interaction.user.voice.channel
        overwrite_everyone = channel.overwrites_for(interaction.guild.default_role)
        overwrite_everyone.connect = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite_everyone)
        await interaction.followup.send("🔒 Đã khóa phòng thoại!", ephemeral=True)

    @discord.ui.button(label="Mở khóa", style=discord.ButtonStyle.secondary, emoji="🔓", row=0, custom_id="vc_unlock")
    async def unlock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
        channel = interaction.user.voice.channel
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.connect = None
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.followup.send("🔓 Đã mở khóa phòng thoại!", ephemeral=True)

    @discord.ui.button(label="Ẩn", style=discord.ButtonStyle.secondary, emoji="🥷", row=0, custom_id="vc_hide")
    async def hide_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
        channel = interaction.user.voice.channel
        overwrite_everyone = channel.overwrites_for(interaction.guild.default_role)
        overwrite_everyone.view_channel = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite_everyone)
        await interaction.followup.send("🥷 Đã ẩn phòng thoại!", ephemeral=True)

    @discord.ui.button(label="Hiện", style=discord.ButtonStyle.secondary, emoji="👁️", row=0, custom_id="vc_unhide")
    async def unhide_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
        channel = interaction.user.voice.channel
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.view_channel = None
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.followup.send("👁️ Đã hiện lại phòng thoại!", ephemeral=True)

    @discord.ui.button(label="Giới hạn", style=discord.ButtonStyle.secondary, emoji="👥", row=1, custom_id="vc_limit")
    async def limit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LimitModal())

    @discord.ui.button(label="Mời", style=discord.ButtonStyle.secondary, emoji="➕", row=1, custom_id="vc_invite")
    async def invite_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("➕ Dùng lệnh `/room-allow user:@tên` để cho phép thành viên tham gia!", ephemeral=True)

    @discord.ui.button(label="Cấm", style=discord.ButtonStyle.secondary, emoji="🚫", row=1, custom_id="vc_ban")
    async def ban_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🚫 Dùng lệnh `/room-deny user:@tên` hoặc `/room-kick user:@tên`!", ephemeral=True)

    @discord.ui.button(label="Đổi tên", style=discord.ButtonStyle.secondary, emoji="✏️", row=2, custom_id="vc_rename")
    async def rename_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RenameModal())

    @discord.ui.button(label="Khu vực", style=discord.ButtonStyle.secondary, emoji="🪪", row=2, custom_id="vc_region")
    async def region_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🌐 Chọn khu vực máy chủ bên dưới:", view=RegionView(), ephemeral=True)

    @discord.ui.button(label="Reset room", style=discord.ButtonStyle.secondary, emoji="🖼️", row=2, custom_id="vc_reset")
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
        channel = interaction.user.voice.channel
        await channel.edit(name=f"{ROOM_PREFIX} Phòng của {interaction.user.display_name}", user_limit=0, rtc_region=None)
        await channel.set_permissions(interaction.guild.default_role, overwrite=None)
        await interaction.followup.send("🖼️ Đã khôi phục cài đặt phòng về mặc định!", ephemeral=True)

    @discord.ui.button(label="Nhận chủ", style=discord.ButtonStyle.secondary, emoji="👑", row=3, custom_id="vc_claim")
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại!", ephemeral=True)
        channel = interaction.user.voice.channel
        room = get_room(channel.id)
        if not room:
            return await interaction.response.send_message("❌ Đây không phải phòng tạm thời!", ephemeral=True)
        
        owner = interaction.guild.get_member(room["owner_id"])
        if owner and owner in channel.members:
            return await interaction.response.send_message("❌ Chủ sở hữu vẫn đang ở trong phòng!", ephemeral=True)
        
        save_room(interaction.guild.id, channel.id, interaction.user.id, channel.category_id if channel.category else 0)
        await interaction.response.send_message(f"👑 **{interaction.user.display_name}** đã trở thành chủ phòng mới!", ephemeral=True)

    @discord.ui.button(label="Chuyển chủ", style=discord.ButtonStyle.secondary, emoji="🧹", row=3, custom_id="vc_transfer")
    async def transfer_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TransferModal())

# -----------------------------
# Bot Class & Events
# -----------------------------
class TempVoiceBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(VoiceControlView())
        await self.tree.sync()
        log.info("Đã đồng bộ Slash Commands & Permanent Views!")

bot = TempVoiceBot()

async def create_room(guild: discord.Guild, member: discord.Member):
    row = get_generator(guild.id)
    category = guild.get_channel(row["category_id"]) if row else None

    existing = get_owned_room(guild.id, member.id)
    if existing:
        old = guild.get_channel(existing["channel_id"])
        if isinstance(old, discord.VoiceChannel):
            try:
                await member.move_to(old)
                return old
            except discord.HTTPException:
                pass
        delete_room_record(existing["channel_id"])

    new_channel = await guild.create_voice_channel(
        name=f"{ROOM_PREFIX} Phòng của {member.display_name}",
        category=category
    )

    await member.move_to(new_channel)
    save_room(guild.id, new_channel.id, member.id, category.id if category else 0)

    embed = discord.Embed(
        title="🎛️ BẢNG ĐIỀU KHIỂN PHÒNG THOẠI",
        description=f"Chủ phòng: {member.mention}\n\nNhấp vào các nút bên dưới để quản lý phòng thoại của bạn!",
        color=discord.Color.purple()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Lucky Smile TempVoice")

    await new_channel.send(
        content=f"👋 Chào mừng {member.mention} đến với phòng thoại riêng!",
        embed=embed,
        view=VoiceControlView()
    )
    return new_channel

@bot.event
async def on_ready():
    init_db()
    log.info("Đăng nhập thành công: %s (%s)", bot.user, bot.user.id)

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # 1. Tạo phòng khi bấm vào Generator
    if after.channel and isinstance(after.channel, discord.VoiceChannel):
        row = get_generator(member.guild.id)
        if row and after.channel.id == row["generator_id"]:
            await create_room(member.guild, member)

    # 2. Xóa phòng khi trống người
    if before.channel and isinstance(before.channel, discord.VoiceChannel):
        row = get_generator(member.guild.id)
        if row and before.channel.id == row["generator_id"]:
            return

        room = get_room(before.channel.id)
        if room and len(before.channel.members) == 0:
            await delete_temp_room(before.channel)

# -----------------------------
# Slash Commands
# -----------------------------
@bot.tree.command(name="setup", description="Khởi tạo danh mục và kênh Tạo Phòng")
@app_commands.checks.has_permissions(administrator=True)
async def setup_cmd(interaction: discord.Interaction):
    guild = interaction.guild
    category = discord.utils.get(guild.categories, name=DEFAULT_CATEGORY)
    if not category:
        category = await guild.create_category(DEFAULT_CATEGORY)

    generator = discord.utils.get(category.voice_channels, name=DEFAULT_GENERATOR)
    if not generator:
        generator = await guild.create_voice_channel(DEFAULT_GENERATOR, category=category)

    control = discord.utils.get(category.text_channels, name=DEFAULT_CONTROL)
    if not control:
        control = await guild.create_text_channel(DEFAULT_CONTROL, category=category)

    save_generator(guild.id, category.id, generator.id, control.id)
    await interaction.response.send_message("✅ Khởi tạo hệ thống TempVoice thành công!", ephemeral=True)

@bot.tree.command(name="room-allow", description="Cho phép thành viên tham gia phòng thoại")
async def room_allow(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
    channel = interaction.user.voice.channel
    overwrite = channel.overwrites_for(user)
    overwrite.connect = True
    overwrite.view_channel = True
    await channel.set_permissions(user, overwrite=overwrite)
    await interaction.response.send_message(f"✅ Đã cấp quyền cho {user.mention} vào phòng!", ephemeral=True)

@bot.tree.command(name="room-deny", description="Cấm thành viên tham gia phòng thoại")
async def room_deny(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
    channel = interaction.user.voice.channel
    overwrite = channel.overwrites_for(user)
    overwrite.connect = False
    await channel.set_permissions(user, overwrite=overwrite)
    if user.voice and user.voice.channel == channel:
        await user.move_to(None)
    await interaction.response.send_message(f"🚫 Đã cấm {user.mention} vào phòng!", ephemeral=True)

@bot.tree.command(name="room-kick", description="Đuổi thành viên ra khỏi phòng thoại")
async def room_kick(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
    channel = interaction.user.voice.channel
    if user.voice and user.voice.channel == channel:
        await user.move_to(None)
        await interaction.response.send_message(f"👞 Đã đuổi {user.mention} ra khỏi phòng!", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Thành viên {user.mention} không ở trong phòng của bạn!", ephemeral=True)

if __name__ == "__main__":
    init_db()
    bot.run(TOKEN)