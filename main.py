import os
import sqlite3
import logging
from datetime import datetime
from typing import Optional
from aiohttp import web

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "tempvoice.db")
DEFAULT_CATEGORY_ID = 1304790158763098224
DEFAULT_GENERATOR = os.getenv("GENERATOR_NAME", "➕・Tạo Phòng")
DEFAULT_CONTROL = os.getenv("CONTROL_CHANNEL_NAME", "🎛️・quản-lý-phòng")
FIXED_BLOG_NAME = os.getenv("BLOG_CHANNEL_NAME", "💬│blog-chat")
ROOM_PREFIX = os.getenv("ROOM_PREFIX", "🔊")

if not TOKEN:
    raise RuntimeError("Chưa có DISCORD_TOKEN trong môi trường!")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("VoiceChatBot")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True
intents.message_content = True  # Quan trọng để đọc log nội dung chat văn bản

# -----------------------------
# Web Server (Chống sleep trên Railway)
# -----------------------------
async def handle(request):
    return web.Response(text="Voice Chat Bot is alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info(f"Web server đã chạy trên cổng {port}")

# -----------------------------
# Database Management
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
            control_channel_id INTEGER,
            blog_channel_id INTEGER
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
    # Tự động update schema nếu thiếu cột blog_channel_id ở bản cũ
    try:
        con.execute("ALTER TABLE generators ADD COLUMN blog_channel_id INTEGER")
    except sqlite3.OperationalError:
        pass
    con.commit()
    con.close()

def get_generator(guild_id: int):
    con = db()
    row = con.execute("SELECT * FROM generators WHERE guild_id = ?", (guild_id,)).fetchone()
    con.close()
    return row

def save_generator(guild_id, category_id, generator_id, control_channel_id, blog_channel_id):
    con = db()
    con.execute("""
        INSERT INTO generators(guild_id, category_id, generator_id, control_channel_id, blog_channel_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            category_id=excluded.category_id,
            generator_id=excluded.generator_id,
            control_channel_id=excluded.control_channel_id,
            blog_channel_id=excluded.blog_channel_id
    """, (guild_id, category_id, generator_id, control_channel_id, blog_channel_id))
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

# Hàm gửi log tập trung về kênh Blog
async def send_blog_log(guild: discord.Guild, title: str, description: str, color: discord.Color):
    try:
        gen = get_generator(guild.id)
        if gen and gen["blog_channel_id"]:
            blog_ch = guild.get_channel(gen["blog_channel_id"])
            if blog_ch:
                now_str = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
                embed = discord.Embed(title=title, description=description, color=color)
                embed.set_footer(text=f"Thời gian: {now_str} • Voice Chat System")
                await blog_ch.send(embed=embed)
    except Exception as e:
        log.error(f"Không thể gửi blog log: {e}")

# -----------------------------
# Modals (Giao diện nhập liệu hiện đại)
# -----------------------------
class LimitModal(discord.ui.Modal, title="⚙️ Giới hạn số lượng thành viên"):
    limit = discord.ui.TextInput(
        label="Số lượng tối đa (0 = Không giới hạn)",
        placeholder="Nhập số từ 1 đến 99...",
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
        await interaction.response.send_message(f"✅ Đã cập nhật giới hạn phòng thành **{val}** người.", ephemeral=True)

class RenameModal(discord.ui.Modal, title="✏️ Đổi tên phòng thoại"):
    new_name = discord.ui.TextInput(
        label="Tên phòng mới",
        placeholder="Nhập tên phòng mới của bạn...",
        min_length=1,
        max_length=100,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)

        channel = interaction.user.voice.channel
        old_name = channel.name
        new_room_name = f"{ROOM_PREFIX} {self.new_name.value}"
        await channel.edit(name=new_room_name)

        await send_blog_log(
            interaction.guild,
            "✏️ ĐỔI TÊN PHÒNG",
            f"• **Chủ phòng**: {interaction.user.mention}\n• **Tên cũ**: `{old_name}`\n• **Tên mới**: `{new_room_name}`",
            discord.Color.gold()
        )
        await interaction.response.send_message(f"✅ Đã đổi tên phòng thành: **{self.new_name.value}**", ephemeral=True)

class TransferModal(discord.ui.Modal, title="👑 Chuyển quyền chủ phòng"):
    user_id = discord.ui.TextInput(
        label="ID Discord của thành viên nhận quyền",
        placeholder="Ví dụ: 123456789012345678",
        max_length=20
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.user.voice.channel if interaction.user.voice else None
        if not channel:
            return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm.", ephemeral=True)
        row = get_room(channel.id)
        if not row or row["owner_id"] != interaction.user.id:
            return await interaction.response.send_message("❌ Chỉ chủ phòng mới có quyền này.", ephemeral=True)
        try:
            uid = int(str(self.user_id).strip())
        except ValueError:
            return await interaction.response.send_message("❌ ID Discord không hợp lệ.", ephemeral=True)

        member = interaction.guild.get_member(uid)
        if not member:
            return await interaction.response.send_message("❌ Không tìm thấy thành viên này trong máy chủ.", ephemeral=True)
        if not member.voice or member.voice.channel.id != channel.id:
            return await interaction.response.send_message("❌ Người nhận quyền phải đang ở trong cùng phòng thoại với bạn.", ephemeral=True)

        save_room(interaction.guild.id, channel.id, member.id, channel.category_id if channel.category else 0)
        await send_blog_log(
            interaction.guild,
            "👑 CHUYỂN QUYỀN CHỦ PHÒNG",
            f"• **Phòng**: {channel.mention}\n• **Chủ cũ**: {interaction.user.mention}\n• **Chủ mới**: {member.mention}",
            discord.Color.blue()
        )
        await interaction.response.send_message(f"👑 Đã chuyển quyền chủ phòng cho {member.mention}.", ephemeral=True)

# -----------------------------
# Views & Controls (Giao diện nút bấm tối giản, chuyên nghiệp)
# -----------------------------
class RegionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Tự động (Automatic)", value="auto", description="Để hệ thống tự chọn tối ưu"),
            discord.SelectOption(label="Singapore", value="singapore", description="Khu vực Singapore"),
            discord.SelectOption(label="Hong Kong", value="hongkong", description="Khu vực Hong Kong"),
            discord.SelectOption(label="Japan", value="japan", description="Khu vực Nhật Bản"),
            discord.SelectOption(label="US Central", value="us-central", description="Trung Mỹ"),
            discord.SelectOption(label="US East", value="us-east", description="Đông Mỹ"),
        ]
        super().__init__(placeholder="🌐 Chọn khu vực máy chủ âm thanh...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại!", ephemeral=True)
        
        channel = interaction.user.voice.channel
        region_val = None if self.values[0] == "auto" else self.values[0]
        try:
            await channel.edit(rtc_region=region_val)
            region_name = "Tự động" if self.values[0] == "auto" else self.values[0].upper()
            await interaction.response.send_message(f"🌐 Đã chuyển khu vực máy chủ âm thanh sang: **{region_name}**", ephemeral=True)
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
        try:
            overwrite_everyone = channel.overwrites_for(interaction.guild.default_role)
            overwrite_everyone.connect = False
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite_everyone)
            
            overwrite_owner = channel.overwrites_for(interaction.user)
            overwrite_owner.connect = True
            await channel.set_permissions(interaction.user, overwrite=overwrite_owner)

            await interaction.followup.send("🔒 Đã khóa phòng thoại thành công!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ Bot thiếu quyền quản lý kênh!", ephemeral=True)

    @discord.ui.button(label="Mở khóa", style=discord.ButtonStyle.secondary, emoji="🔓", row=0, custom_id="vc_unlock")
    async def unlock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
        
        channel = interaction.user.voice.channel
        try:
            overwrite = channel.overwrites_for(interaction.guild.default_role)
            overwrite.connect = None
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
            await interaction.followup.send("🔓 Đã mở khóa phòng thoại thành công!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ Bot thiếu quyền quản lý kênh!", ephemeral=True)

    @discord.ui.button(label="Ẩn", style=discord.ButtonStyle.secondary, emoji="🥷", row=0, custom_id="vc_hide")
    async def hide_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
        channel = interaction.user.voice.channel
        overwrite_everyone = channel.overwrites_for(interaction.guild.default_role)
        overwrite_everyone.view_channel = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite_everyone)
        await interaction.followup.send("🥷 Đã ẩn phòng thoại khỏi danh sách kênh!", ephemeral=True)

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
        await interaction.response.send_message("➕ Sử dụng lệnh `/room-allow user:@tên` để cho phép thành viên tham gia phòng!", ephemeral=True)

    @discord.ui.button(label="Cấm", style=discord.ButtonStyle.secondary, emoji="🚫", row=1, custom_id="vc_ban")
    async def ban_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🚫 Sử dụng lệnh `/room-deny user:@tên` hoặc `/room-kick user:@tên`!", ephemeral=True)

    @discord.ui.button(label="Đổi tên", style=discord.ButtonStyle.secondary, emoji="✏️", row=2, custom_id="vc_rename")
    async def rename_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RenameModal())

    @discord.ui.button(label="Khu vực", style=discord.ButtonStyle.secondary, emoji="🌐", row=2, custom_id="vc_region")
    async def region_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🌐 Chọn khu vực máy chủ âm thanh:", view=RegionView(), ephemeral=True)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.secondary, emoji="🔄", row=2, custom_id="vc_reset")
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
        channel = interaction.user.voice.channel
        await channel.edit(name=f"{ROOM_PREFIX} Phòng của {interaction.user.display_name}", user_limit=0, rtc_region=None)
        await channel.set_permissions(interaction.guild.default_role, overwrite=None)
        await interaction.followup.send("🔄 Đã khôi phục cài đặt gốc cho phòng!", ephemeral=True)

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
            return await interaction.response.send_message("❌ Chủ sở hữu hiện tại vẫn đang ở trong phòng!", ephemeral=True)
        
        save_room(interaction.guild.id, channel.id, interaction.user.id, channel.category_id if channel.category else 0)
        await send_blog_log(
            interaction.guild,
            "👑 NHẬN QUYỀN CHỦ PHÒNG",
            f"• **Phòng**: {channel.mention}\n• **Thành viên nhận chủ**: {interaction.user.mention}",
            discord.Color.purple()
        )
        await interaction.response.send_message(f"👑 **{interaction.user.display_name}** đã tiếp quản quyền chủ phòng!", ephemeral=True)

    @discord.ui.button(label="Chuyển chủ", style=discord.ButtonStyle.secondary, emoji="📤", row=3, custom_id="vc_transfer")
    async def transfer_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TransferModal())

# -----------------------------
# Bot Logic & Main Event Handlers
# -----------------------------
class VoiceChatBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(VoiceControlView())
        await self.tree.sync()
        self.loop.create_task(start_web_server())
        log.info("Voice Chat Bot đã đồng bộ Slash Commands, Views & Web Server!")

bot = VoiceChatBot()

async def create_room(guild: discord.Guild, member: discord.Member, category_id: int):
    category = guild.get_channel(category_id)
    if not category or not isinstance(category, discord.CategoryChannel):
        log.error(f"Không tìm thấy danh mục ID {category_id}")
        return None

    # Kiểm tra nếu user đã có phòng cũ, dọn dẹp hoặc cho di chuyển lại
    existing = get_owned_room(guild.id, member.id)
    if existing:
        old = guild.get_channel(existing["channel_id"])
        if isinstance(old, discord.VoiceChannel):
            try:
                await member.move_to(old)
                await send_blog_log(
                    guild,
                    "🔁 TÁI SỬ DỤNG PHÒNG",
                    f"• **Thành viên**: {member.mention}\n• **Phòng**: {old.mention}",
                    discord.Color.dark_grey()
                )
                return old
            except discord.HTTPException:
                pass
        delete_room_record(existing["channel_id"])

    # Tạo phòng thoại mới chính xác tại danh mục yêu cầu
    new_channel = await guild.create_voice_channel(
        name=f"{ROOM_PREFIX} Phòng của {member.display_name}",
        category=category
    )

    await member.move_to(new_channel)
    save_room(guild.id, new_channel.id, member.id, category.id)

    # Gửi log tạo phòng lên kênh blog tập trung
    await send_blog_log(
        guild,
        "✨ TẠO PHÒNG THOẠI MỚI",
        f"• **Chủ phòng**: {member.mention}\n• **Phòng tạo**: {new_channel.mention}\n• **Danh mục**: `{category.name}`",
        discord.Color.green()
    )

    embed = discord.Embed(
        title="🎛️ BẢNG ĐIỀU KHIỂN PHÒNG",
        description=f"Chủ phòng: {member.mention}\n\nSử dụng các nút bên dưới để tùy chỉnh không gian riêng của bạn.",
        color=discord.Color.blurple()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Voice Chat System • Professional Management")

    await new_channel.send(
        content=f"👋 Chào mừng {member.mention} đến với phòng thoại của bạn!",
        embed=embed,
        view=VoiceControlView()
    )
    return new_channel

@bot.event
async def on_ready():
    init_db()
    log.info("Đăng nhập thành công bot: %s (%s)", bot.user, bot.user.id)

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # Xử lý khi user vào kênh Tạo Phòng (Generator)
    if after.channel and isinstance(after.channel, discord.VoiceChannel):
        row = get_generator(member.guild.id)
        # Ràng buộc chặt chẽ: chỉ khi đúng ID nút tạo phòng và đúng danh mục đã setup -> không bị spam lung tung
        if row and after.channel.id == row["generator_id"]:
            if after.channel.category_id == row["category_id"]:
                await create_room(member.guild, member, row["category_id"])

    # Xử lý khi user rời khỏi phòng tạm (nếu phòng trống thì tự động xóa)
    if before.channel and isinstance(before.channel, discord.VoiceChannel):
        row = get_generator(member.guild.id)
        if row and before.channel.id == row["generator_id"]:
            return

        room = get_room(before.channel.id)
        if room and len(before.channel.members) == 0:
            room_id = before.channel.id
            room_name = before.channel.name
            delete_room_record(room_id)
            try:
                await before.channel.delete(reason="Voice Chat: Xóa phòng trống tự động")
                await send_blog_log(
                    member.guild,
                    "🗑️ XÓA PHÒNG TRỐNG",
                    f"• **Phòng đã xóa**: `{room_name}`\n• **Lý do**: Không còn thành viên nào trong phòng.",
                    discord.Color.red()
                )
            except discord.HTTPException:
                pass

# Bắt sự kiện tin nhắn chat để log nội dung vào blog phục vụ kiểm duyệt (tùy chọn hoặc kiểm duyệt toàn server)
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    
    # Kiểm tra nếu tin nhắn được gửi vào một trong các phòng tạm thời hoặc kênh trong hệ thống
    room = get_room(message.channel.id)
    if room:
        await send_blog_log(
            message.guild,
            "💬 NHẬT KÝ TIN NHẮN PHÒNG TẠM",
            f"• **Người gửi**: {message.author.mention} (`{message.author}`)\n• **Phòng**: {message.channel.mention}\n• **Nội dung**: {message.content or '[Tệp đính kèm / Hình ảnh]'}",
            discord.Color.light_embed()
        )
    await bot.process_commands(message)

# -----------------------------
# Slash Commands (Quản lý hệ thống chuẩn mực)
# -----------------------------
@bot.tree.command(name="setup", description="Khởi tạo hệ thống Voice Chat và Kênh Blog tập trung")
@app_commands.checks.has_permissions(administrator=True)
async def setup_cmd(interaction: discord.Interaction):
    guild = interaction.guild
    category = guild.get_channel(DEFAULT_CATEGORY_ID)
    
    if not category or not isinstance(category, discord.CategoryChannel):
        return await interaction.response.send_message(
            f"❌ Không tìm thấy Danh mục có ID `{DEFAULT_CATEGORY_ID}` trên Server này!", 
            ephemeral=True
        )

    # Tạo nút tạo phòng đúng danh mục quy định
    generator = discord.utils.get(category.voice_channels, name=DEFAULT_GENERATOR)
    if not generator:
        generator = await guild.create_voice_channel(DEFAULT_GENERATOR, category=category)

    control = discord.utils.get(category.text_channels, name=DEFAULT_CONTROL)
    if not control:
        control = await guild.create_text_channel(DEFAULT_CONTROL, category=category)

    # Tạo kênh blog chat tập trung cho kiểm duyệt viên
    blog_channel = discord.utils.get(category.text_channels, name=FIXED_BLOG_NAME)
    if not blog_channel:
        blog_channel = await guild.create_text_channel(
            FIXED_BLOG_NAME, 
            category=category, 
            topic="Kênh nhật ký blog tập trung ghi nhận toàn bộ hoạt động tạo/xóa phòng và nội dung chat phục vụ kiểm duyệt."
        )

    save_generator(guild.id, category.id, generator.id, control.id, blog_channel.id)
    
    await send_blog_log(
        guild,
        "⚙️ HỆ THỐNG KHỞI TẠO THÀNH CÔNG",
        f"• **Người thiết lập**: {interaction.user.mention}\n• **Danh mục quản lý**: `{category.name}`\n• **Kênh Blog tập trung**: {blog_channel.mention}",
        discord.Color.teal()
    )
    
    await interaction.response.send_message(f"✅ Khởi tạo hệ thống Voice Chat & Kênh Blog tập trung (`{FIXED_BLOG_NAME}`) thành công!", ephemeral=True)

@bot.tree.command(name="room-allow", description="Cho phép thành viên tham gia phòng thoại")
async def room_allow(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
    channel = interaction.user.voice.channel
    overwrite = channel.overwrites_for(user)
    overwrite.connect = True
    overwrite.view_channel = True
    await channel.set_permissions(user, overwrite=overwrite)
    await interaction.response.send_message(f"✅ Đã cấp quyền cho {user.mention} tham gia phòng.", ephemeral=True)

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
    await interaction.response.send_message(f"🚫 Đã cấm {user.mention} truy cập phòng.", ephemeral=True)

@bot.tree.command(name="room-kick", description="Đuổi thành viên ra khỏi phòng thoại")
async def room_kick(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
    channel = interaction.user.voice.channel
    if user.voice and user.voice.channel == channel:
        await user.move_to(None)
        await interaction.response.send_message(f"👞 Đã đá {user.mention} ra khỏi phòng.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Thành viên {user.mention} không ở trong phòng của bạn.", ephemeral=True)

if __name__ == "__main__":
    init_db()
    bot.run(TOKEN)
