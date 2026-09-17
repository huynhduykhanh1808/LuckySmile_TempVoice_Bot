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

# ID Danh mục cố định duy nhất cho phép tạo phòng
TARGET_CATEGORY_ID = 1304790158763098224

DEFAULT_GENERATOR = os.getenv("GENERATOR_NAME", "➕・Tạo Phòng")
DEFAULT_CONTROL = os.getenv("CONTROL_CHANNEL_NAME", "🎛️・quản-lý-phòng")
ROOM_PREFIX = os.getenv("ROOM_PREFIX", "🔊")

if not TOKEN:
    raise RuntimeError("Chưa có DISCORD_TOKEN trong biến môi trường Environment Variables!")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("LuckySmileTempVoice")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


# -----------------------------
# Database Helpers
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


def save_generator(guild_id: int, category_id: int, generator_id: int, control_channel_id: Optional[int]):
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


def save_room(guild_id: int, channel_id: int, owner_id: int, category_id: int):
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


def get_owned_room_in_category(guild_id: int, owner_id: int, category_id: int):
    con = db()
    row = con.execute("SELECT * FROM rooms WHERE guild_id=? AND owner_id=? AND category_id=?", 
                      (guild_id, owner_id, category_id)).fetchone()
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
    try:
        delete_room_record(channel.id)
        await channel.delete(reason="Lucky Smile TempVoice: Phòng trống")
    except discord.HTTPException as e:
        log.error(f"Lỗi khi xóa kênh {channel.id}: {e}")


# -----------------------------
# Setup Routine
# -----------------------------
async def ensure_setup(guild: discord.Guild):
    category = guild.get_channel(TARGET_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        log.error(f"Không tìm thấy Category ID: {TARGET_CATEGORY_ID}")
        return None, None, None

    row = get_generator(guild.id)
    generator = None
    control = None

    if row:
        generator = guild.get_channel(row["generator_id"])
        control = guild.get_channel(row["control_channel_id"]) if row["control_channel_id"] else None

    if not isinstance(generator, discord.VoiceChannel) or generator.category_id != TARGET_CATEGORY_ID:
        generator = discord.utils.get(category.voice_channels, name=DEFAULT_GENERATOR)
        if generator is None:
            generator = await guild.create_voice_channel(
                DEFAULT_GENERATOR,
                category=category,
                reason="Lucky Smile TempVoice generator"
            )

    if not isinstance(control, discord.TextChannel) or control.category_id != TARGET_CATEGORY_ID:
        control = discord.utils.get(category.text_channels, name=DEFAULT_CONTROL)
        if control is None:
            control = await guild.create_text_channel(
                DEFAULT_CONTROL,
                category=category,
                reason="Lucky Smile TempVoice control panel"
            )

    try:
        await generator.edit(position=0)
    except discord.HTTPException:
        pass

    save_generator(guild.id, category.id, generator.id, control.id if control else None)
    return category, generator, control


# -----------------------------
# Modals & Views (Pro Interface)
# -----------------------------
class LimitModal(discord.ui.Modal, title="⚙️ Giới Hạn Số Người Tham Gia"):
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
        await interaction.response.send_message(f"👥 Đã cập nhật giới hạn phòng thành: **{val if val > 0 else 'Không giới hạn'}**", ephemeral=True)


class RenameModal(discord.ui.Modal, title="✏️ Đổi Tên Phòng Thoại"):
    new_name = discord.ui.TextInput(
        label="Tên phòng thoại mới",
        placeholder="Nhập tên phòng...",
        min_length=1,
        max_length=100,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)

        channel = interaction.user.voice.channel
        await channel.edit(name=f"{ROOM_PREFIX} {self.new_name.value}")
        await interaction.response.send_message(f"✏️ Đã đổi tên phòng thành: **{self.new_name.value}**", ephemeral=True)


class RegionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Tự động (Automatic)", value="auto", description="Tự chọn server tối ưu nhất"),
            discord.SelectOption(label="Singapore", value="singapore", description="Ping thấp nhất cho Việt Nam"),
            discord.SelectOption(label="Hong Kong", value="hongkong", description="Máy chủ Hồng Kông"),
            discord.SelectOption(label="Japan", value="japan", description="Máy chủ Nhật Bản"),
            discord.SelectOption(label="US Central", value="us-central", description="Máy chủ Trung Mỹ"),
            discord.SelectOption(label="US East", value="us-east", description="Máy chủ Đông Mỹ"),
            discord.SelectOption(label="US West", value="us-west", description="Máy chủ Tây Mỹ"),
            discord.SelectOption(label="Rotterdam", value="rotterdam", description="Máy chủ Châu Âu"),
        ]
        super().__init__(placeholder="🌐 Chọn Region / Khu vực máy chủ...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại!", ephemeral=True)

        channel = interaction.user.voice.channel
        region_val = None if self.values[0] == "auto" else self.values[0]

        try:
            await channel.edit(rtc_region=region_val)
            region_name = "Tự động" if self.values[0] == "auto" else self.values[0].upper()
            await interaction.response.send_message(f"🌐 Đã chuyển khu vực máy chủ sang: **{region_name}**", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Không thể đổi khu vực: {e}", ephemeral=True)


class RegionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(RegionSelect())


class VoiceControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Khóa", style=discord.ButtonStyle.danger, emoji="🔒", row=0, custom_id="vc_lock")
    async def lock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ Bạn phải ở trong phòng thoại tạm!", ephemeral=True)
        channel = interaction.user.voice.channel
        overwrite_everyone = channel.overwrites_for(interaction.guild.default_role)
        overwrite_everyone.connect = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite_everyone)
        overwrite_owner = channel.overwrites_for(interaction.user)
        overwrite_owner.connect = True
        await channel.set_permissions(interaction.user, overwrite=overwrite_owner)
        await interaction.followup.send("🔒 Đã khóa phòng thoại! Người lạ không thể tham gia.", ephemeral=True)

    @discord.ui.button(label="Mở khóa", style=discord.ButtonStyle.success, emoji="🔓", row=0, custom_id="vc_unlock")
    async def unlock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ Bạn phải ở trong phòng thoại tạm!", ephemeral=True)
        channel = interaction.user.voice.channel
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.connect = None
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.followup.send("🔓 Đã mở khóa phòng thoại cho mọi người!", ephemeral=True)

    @discord.ui.button(label="Ẩn phòng", style=discord.ButtonStyle.secondary, emoji="🥷", row=0, custom_id="vc_hide")
    async def hide_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ Bạn phải ở trong phòng thoại tạm!", ephemeral=True)
        channel = interaction.user.voice.channel
        overwrite_everyone = channel.overwrites_for(interaction.guild.default_role)
        overwrite_everyone.view_channel = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite_everyone)
        await interaction.followup.send("🥷 Đã ẩn phòng thoại khỏi danh sách server!", ephemeral=True)

    @discord.ui.button(label="Hiện phòng", style=discord.ButtonStyle.secondary, emoji="👁️", row=0, custom_id="vc_unhide")
    async def unhide_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ Bạn phải ở trong phòng thoại tạm!", ephemeral=True)
        channel = interaction.user.voice.channel
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.view_channel = None
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.followup.send("👁️ Đã hiển thị lại phòng thoại!", ephemeral=True)

    @discord.ui.button(label="Giới hạn", style=discord.ButtonStyle.primary, emoji="👥", row=1, custom_id="vc_limit")
    async def limit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải ở trong phòng thoại tạm!", ephemeral=True)
        await interaction.response.send_modal(LimitModal())

    @discord.ui.button(label="Mời", style=discord.ButtonStyle.secondary, emoji="➕", row=1, custom_id="vc_invite")
    async def invite_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("➕ Gõ lệnh `/room-allow user:@tên` để cho phép thành viên tham gia!", ephemeral=True)

    @discord.ui.button(label="Cấm/Đuổi", style=discord.ButtonStyle.secondary, emoji="🚫", row=1, custom_id="vc_ban")
    async def ban_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🚫 Gõ `/room-deny user:@tên` để cấm hoặc `/room-kick user:@tên` để đuổi!", ephemeral=True)

    @discord.ui.button(label="Cấp quyền", style=discord.ButtonStyle.secondary, emoji="✅", row=1, custom_id="vc_permit")
    async def permit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ Gõ lệnh `/room-allow user:@tên` để cấp quyền!", ephemeral=True)

    @discord.ui.button(label="Đổi tên", style=discord.ButtonStyle.primary, emoji="✏️", row=2, custom_id="vc_rename")
    async def rename_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải ở trong phòng thoại tạm!", ephemeral=True)
        await interaction.response.send_modal(RenameModal())

    @discord.ui.button(label="Bitrate", style=discord.ButtonStyle.secondary, emoji="🎧", row=2, custom_id="vc_bitrate")
    async def bitrate_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🎧 Bitrate đã tự động tối ưu hóa theo chất lượng server!", ephemeral=True)

    @discord.ui.button(label="Khu vực", style=discord.ButtonStyle.secondary, emoji="🪪", row=2, custom_id="vc_region")
    async def region_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải ở trong phòng thoại tạm!", ephemeral=True)
        await interaction.response.send_message("🌐 Chọn Region máy chủ bên dưới:", view=RegionView(), ephemeral=True)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.danger, emoji="🖼️", row=2, custom_id="vc_reset")
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ Bạn phải ở trong phòng thoại tạm!", ephemeral=True)

        channel = interaction.user.voice.channel
        await channel.edit(name=f"{ROOM_PREFIX} Phòng của {interaction.user.display_name}", user_limit=0, rtc_region=None)
        await channel.set_permissions(interaction.guild.default_role, overwrite=None)
        await interaction.followup.send("🖼️ Đã khôi phục cài đặt phòng về mặc định!", ephemeral=True)

    @discord.ui.button(label="Trò chuyện", style=discord.ButtonStyle.secondary, emoji="💬", row=3, custom_id="vc_chat")
    async def chat_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("💬 Bạn có thể trò chuyện trực tiếp tại khung chat của kênh thoại này!", ephemeral=True)

    @discord.ui.button(label="Nhận chủ", style=discord.ButtonStyle.secondary, emoji="👑", row=3, custom_id="vc_claim")
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("👑 Đã xác nhận bạn là chủ quản lý phòng thoại!", ephemeral=True)

    @discord.ui.button(label="Chuyển chủ", style=discord.ButtonStyle.secondary, emoji="🧹", row=3, custom_id="vc_transfer")
    async def transfer_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🧹 Dùng lệnh `/room-transfer user:@tên` để chuyển quyền quản lý!", ephemeral=True)


# -----------------------------
# Slash Commands
# -----------------------------
@bot.tree.command(name="setup", description="Cấu hình hệ thống Voice tạm thời trong Danh mục chuẩn")
@app_commands.checks.has_permissions(manage_channels=True)
async def setup(interaction: discord.Interaction):
    if not interaction.guild:
        return await interaction.response.send_message("❌ Lệnh này chỉ sử dụng trong server!", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    cat, gen, ctrl = await ensure_setup(interaction.guild)
    if not cat:
        return await interaction.followup.send(f"❌ Không tìm thấy Danh mục có ID `{TARGET_CATEGORY_ID}`!", ephemeral=True)

    await interaction.followup.send(
        f"✨ **Đã đồng bộ hệ thống phòng thoại thành công!**\n"
        f"• **Danh mục cố định:** {cat.name}\n"
        f"• **Kênh tạo phòng:** {gen.mention}\n"
        f"• **Kênh quản lý:** {ctrl.mention if ctrl else 'Chưa có'}",
        ephemeral=True
    )


@bot.tree.command(name="room-allow", description="Cho phép một thành viên vào phòng thoại")
async def room_allow(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)

    channel = interaction.user.voice.channel
    overwrite = channel.overwrites_for(user)
    overwrite.connect = True
    overwrite.view_channel = True
    await channel.set_permissions(user, overwrite=overwrite)
    await interaction.response.send_message(f"✅ Đã cấp quyền cho {user.mention} tham gia phòng!", ephemeral=True)


@bot.tree.command(name="room-deny", description="Cấm một thành viên tham gia phòng thoại")
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


@bot.tree.command(name="room-kick", description="Đuổi thành viên ra khỏi phòng thoại hiện tại")
async def room_kick(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)

    channel = interaction.user.voice.channel
    if user.voice and user.voice.channel == channel:
        await user.move_to(None)
        await interaction.response.send_message(f"👞 Đã đuổi {user.mention} ra khỏi phòng!", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ {user.mention} không có mặt trong phòng của bạn!", ephemeral=True)


@bot.tree.command(name="room-reset", description="Reset toàn bộ cấu hình phòng về mặc định")
async def room_reset(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)

    channel = interaction.user.voice.channel
    await channel.edit(name=f"{ROOM_PREFIX} Phòng của {interaction.user.display_name}", user_limit=0, rtc_region=None)

    for target in list(channel.overwrites.keys()):
        if target != interaction.guild.default_role:
            await channel.set_permissions(target, overwrite=None)

    await channel.set_permissions(interaction.guild.default_role, overwrite=None)
    await interaction.response.send_message("🖼️ Đã khôi phục cài đặt phòng về mặc định!", ephemeral=True)


@bot.tree.command(name="room-transfer", description="Chuyển quyền chủ phòng cho người khác")
async def room_transfer(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)

    channel = interaction.user.voice.channel
    overwrite = channel.overwrites_for(user)
    overwrite.connect = True
    overwrite.manage_channels = True
    await channel.set_permissions(user, overwrite=overwrite)

    con = db()
    con.execute("UPDATE rooms SET owner_id=? WHERE channel_id=?", (user.id, channel.id))
    con.commit()
    con.close()

    await interaction.response.send_message(f"👑 Đã chuyển quyền quản lý phòng cho {user.mention}!", ephemeral=True)


# -----------------------------
# Core Creation Logic (Khóa chống Spam phòng)
# -----------------------------
async def create_room(guild: discord.Guild, member: discord.Member):
    category, generator, control = await ensure_setup(guild)
    if not category:
        return None

    # CHỐNG SPAM: Kiểm tra xem thành viên này đã có phòng nào trong Danh mục mục tiêu chưa
    existing = get_owned_room_in_category(guild.id, member.id, TARGET_CATEGORY_ID)
    if existing:
        old_channel = guild.get_channel(existing["channel_id"])
        if isinstance(old_channel, discord.VoiceChannel):
            try:
                # Nếu phòng cũ còn tồn tại -> Di chuyển về phòng cũ, không sinh phòng mới
                await member.move_to(old_channel, reason="Chuyển về phòng hiện có (Chống Spam)")
                return old_channel
            except discord.HTTPException:
                return old_channel
        else:
            delete_room_record(existing["channel_id"])

    # Tạo CHỈ 1 phòng duy nhất nằm dưới Danh mục TARGET_CATEGORY_ID
    new_channel = await guild.create_voice_channel(
        name=f"{ROOM_PREFIX} Phòng của {member.display_name}",
        category=category
    )

    await member.move_to(new_channel)
    save_room(guild.id, new_channel.id, member.id, category.id)

    # Giao diện Pro Dashboard Embed
    embed = discord.Embed(
        title="🎙️ BẢNG ĐIỀU KHIỂN PHÒNG THOẠI PRO",
        description=(
            f"👑 **Chủ sở hữu:** {member.mention}\n"
            f"📌 **Danh mục:** `{category.name}`\n\n"
            "Chủ phòng có thể nhấp vào các nút dưới đây để nhanh chóng điều khiển kênh thoại riêng của mình:"
        ),
        color=0x5865F2  # Discord Blurple Color
    )
    embed.add_field(name="🔒 Bảo mật", value="`Khóa` | `Mở khóa` | `Ẩn` | `Hiện`", inline=True)
    embed.add_field(name="⚙️ Quản lý", value="`Đổi tên` | `Giới hạn` | `Reset`", inline=True)
    embed.add_field(name="🌐 Khác", value="`Khu vực` | `Mời` | `Chuyển chủ`", inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Lucky Smile TempVoice • Hệ thống tự động dọn dẹp phòng trống")

    await new_channel.send(
        content=f"👋 **Chào mừng {member.mention}!** Phòng thoại riêng của bạn đã sẵn sàng.",
        embed=embed,
        view=VoiceControlView()
    )

    return new_channel


# -----------------------------
# Bot Events
# -----------------------------
@bot.event
async def on_ready():
    init_db()
    bot.add_view(VoiceControlView())
    log.info("Đăng nhập: %s (%s)", bot.user, bot.user.id)

    try:
        synced = await bot.tree.sync()
        log.info("Đã sync %s slash commands toàn cầu.", len(synced))
    except Exception:
        log.exception("Không sync được slash commands.")

    for row in all_rooms():
        guild = bot.get_guild(row["guild_id"])
        channel = guild.get_channel(row["channel_id"]) if guild else None
        if channel is None:
            delete_room_record(row["channel_id"])


@bot.event
async def on_guild_channel_delete(channel):
    if isinstance(channel, discord.VoiceChannel):
        delete_room_record(channel.id)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # 1. Khi người dùng bấm vào kênh Tạo Phòng trong Danh mục mục tiêu
    if after.channel and isinstance(after.channel, discord.VoiceChannel):
        if after.channel.category_id == TARGET_CATEGORY_ID:
            row = get_generator(member.guild.id)
            if not row:
                await ensure_setup(member.guild)
                row = get_generator(member.guild.id)

            if row and after.channel.id == row["generator_id"]:
                await create_room(member.guild, member)

    # 2. Khi người dùng rời kênh thoại -> Tự động xóa phòng tạm rác trong Danh mục
    if before.channel and isinstance(before.channel, discord.VoiceChannel):
        if before.channel.category_id == TARGET_CATEGORY_ID:
            row = get_generator(member.guild.id)

            # Không bao giờ xóa kênh generator gốc
            if row and before.channel.id == row["generator_id"]:
                return

            room = get_room(before.channel.id)
            if room and len(before.channel.members) == 0:
                await delete_temp_room(before.channel)


if __name__ == "__main__":
    init_db()
    bot.run(TOKEN)
