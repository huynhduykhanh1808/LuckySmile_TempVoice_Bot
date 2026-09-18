import os
import sqlite3
import logging
from datetime import datetime
from aiohttp import web

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "tempvoice.db")
DEFAULT_GENERATOR = os.getenv("GENERATOR_NAME", "➕・Tạo Phòng")
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
intents.message_content = True

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
            blog_channel_id INTEGER,
            tracked_text_channel_id INTEGER
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
    try:
        con.execute("ALTER TABLE generators ADD COLUMN tracked_text_channel_id INTEGER")
    except sqlite3.OperationalError:
        pass
    con.commit()
    con.close()

def get_generator(guild_id: int):
    con = db()
    row = con.execute("SELECT * FROM generators WHERE guild_id = ?", (guild_id,)).fetchone()
    con.close()
    return row

def save_generator(guild_id, category_id, generator_id, blog_channel_id, tracked_text_channel_id=None):
    con = db()
    con.execute("""
        INSERT INTO generators(guild_id, category_id, generator_id, blog_channel_id, tracked_text_channel_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            category_id=excluded.category_id,
            generator_id=excluded.generator_id,
            blog_channel_id=COALESCE(excluded.blog_channel_id, blog_channel_id),
            tracked_text_channel_id=COALESCE(excluded.tracked_text_channel_id, tracked_text_channel_id)
    """, (guild_id, category_id, generator_id, blog_channel_id, tracked_text_channel_id))
    con.commit()
    con.close()

def update_tracked_channel(guild_id: int, channel_id: int):
    con = db()
    con.execute("UPDATE generators SET tracked_text_channel_id = ? WHERE guild_id = ?", (channel_id, guild_id))
    con.commit()
    con.close()

def clear_tracked_channel(guild_id: int):
    con = db()
    con.execute("UPDATE generators SET tracked_text_channel_id = NULL WHERE guild_id = ?", (guild_id,))
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

# Gửi log tập trung về kênh Blog
async def send_blog_log(guild: discord.Guild, title: str, description: str, color: discord.Color):
    try:
        gen = get_generator(guild.id)
        if gen and gen["blog_channel_id"]:
            blog_ch = guild.get_channel(gen["blog_channel_id"])
            if blog_ch:
                now_str = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
                clean_desc = description.replace("\n", " | ")
                one_line_msg = f"**[{title}]** • {clean_desc} • *({now_str})*"
                await blog_ch.send(one_line_msg)
    except Exception as e:
        log.error(f"Không thể gửi blog log: {e}")

# -----------------------------
# Modals & Views (Điều khiển phòng)
# -----------------------------
class LimitModal(discord.ui.Modal, title="⚙️ Giới hạn số lượng thành viên"):
    limit = discord.ui.TextInput(label="Số lượng tối đa (0 = Không giới hạn)", placeholder="Nhập số từ 1 đến 99...", min_length=1, max_length=2, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
        try:
            val = int(self.limit.value)
            if val < 0 or val > 99: raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ Vui lòng nhập số hợp lệ từ 0 đến 99!", ephemeral=True)
        await interaction.user.voice.channel.edit(user_limit=val)
        await interaction.response.send_message(f"✅ Đã cập nhật giới hạn phòng thành **{val}** người.", ephemeral=True)

class RenameModal(discord.ui.Modal, title="✏️ Đổi tên phòng thoại"):
    new_name = discord.ui.TextInput(label="Tên phòng mới", placeholder="Nhập tên phòng mới...", min_length=1, max_length=100, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
        channel = interaction.user.voice.channel
        old_name = channel.name
        new_room_name = f"{ROOM_PREFIX} {self.new_name.value}"
        await channel.edit(name=new_room_name)
        await send_blog_log(interaction.guild, "✏️ ĐỔI TÊN PHÒNG", f"Chủ phòng: {interaction.user.mention} | Cũ: `{old_name}` | Mới: `{new_room_name}`", discord.Color.gold())
        await interaction.response.send_message(f"✅ Đã đổi tên phòng thành: **{self.new_name.value}**", ephemeral=True)

class TransferModal(discord.ui.Modal, title="👑 Chuyển quyền chủ phòng"):
    user_id = discord.ui.TextInput(label="ID Discord thành viên nhận quyền", placeholder="Ví dụ: 123456789012345678", max_length=20)

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.user.voice.channel if interaction.user.voice else None
        if not channel: return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm.", ephemeral=True)
        row = get_room(channel.id)
        if not row or row["owner_id"] != interaction.user.id:
            return await interaction.response.send_message("❌ Chỉ chủ phòng mới có quyền này.", ephemeral=True)
        try: uid = int(str(self.user_id).strip())
        except ValueError: return await interaction.response.send_message("❌ ID Discord không hợp lệ.", ephemeral=True)
        member = interaction.guild.get_member(uid)
        if not member or not member.voice or member.voice.channel.id != channel.id:
            return await interaction.response.send_message("❌ Thành viên phải đang ở trong phòng với bạn.", ephemeral=True)
        save_room(interaction.guild.id, channel.id, member.id, channel.category_id if channel.category else 0)
        await send_blog_log(interaction.guild, "👑 CHUYỂN CHỦ PHÒNG", f"Phòng: {channel.mention} | Chủ mới: {member.mention}", discord.Color.blue())
        await interaction.response.send_message(f"👑 Đã chuyển quyền chủ phòng cho {member.mention}.", ephemeral=True)

class RegionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Tự động (Automatic)", value="auto"),
            discord.SelectOption(label="Singapore", value="singapore"),
            discord.SelectOption(label="Hong Kong", value="hongkong"),
            discord.SelectOption(label="Japan", value="japan"),
        ]
        super().__init__(placeholder="🌐 Chọn khu vực máy chủ âm thanh...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại!", ephemeral=True)
        region_val = None if self.values[0] == "auto" else self.values[0]
        await interaction.user.voice.channel.edit(rtc_region=region_val)
        await interaction.response.send_message(f"🌐 Đã chuyển khu vực sang: **{self.values[0].upper()}**", ephemeral=True)

class RegionView(discord.ui.View):
    def __init__(self): super().__init__(timeout=60); self.add_item(RegionSelect())

class VoiceControlView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Khóa", style=discord.ButtonStyle.secondary, emoji="🔒", row=0, custom_id="vc_lock")
    async def lock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel: return
        channel = interaction.user.voice.channel
        await channel.set_permissions(interaction.guild.default_role, connect=False)
        await channel.set_permissions(interaction.user, connect=True)
        await interaction.followup.send("🔒 Đã khóa phòng thành công!", ephemeral=True)

    @discord.ui.button(label="Mở khóa", style=discord.ButtonStyle.secondary, emoji="🔓", row=0, custom_id="vc_unlock")
    async def unlock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel: return
        await interaction.user.voice.channel.set_permissions(interaction.guild.default_role, connect=None)
        await interaction.followup.send("🔓 Đã mở khóa phòng thành công!", ephemeral=True)

    @discord.ui.button(label="Ẩn", style=discord.ButtonStyle.secondary, emoji="🥷", row=0, custom_id="vc_hide")
    async def hide_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel: return
        channel = interaction.user.voice.channel
        await channel.set_permissions(interaction.guild.default_role, view_channel=False)
        await interaction.followup.send("🥷 Đã ẩn phòng thoại!", ephemeral=True)

    @discord.ui.button(label="Hiện", style=discord.ButtonStyle.secondary, emoji="👁️", row=0, custom_id="vc_unhide")
    async def unhide_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel: return
        channel = interaction.user.voice.channel
        await channel.set_permissions(interaction.guild.default_role, view_channel=None)
        await interaction.followup.send("👁️ Đã hiện phòng thoại!", ephemeral=True)

    @discord.ui.button(label="Giới hạn", style=discord.ButtonStyle.secondary, emoji="👥", row=1, custom_id="vc_limit")
    async def limit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LimitModal())

    @discord.ui.button(label="Đổi tên", style=discord.ButtonStyle.secondary, emoji="✏️", row=2, custom_id="vc_rename")
    async def rename_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RenameModal())

    @discord.ui.button(label="Khu vực", style=discord.ButtonStyle.secondary, emoji="🌐", row=2, custom_id="vc_region")
    async def region_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🌐 Chọn khu vực máy chủ âm thanh:", view=RegionView(), ephemeral=True)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.secondary, emoji="🔄", row=2, custom_id="vc_reset")
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel: return
        channel = interaction.user.voice.channel
        await channel.edit(name=f"{ROOM_PREFIX} Phòng của {interaction.user.display_name}", user_limit=0, rtc_region=None)
        await channel.set_permissions(interaction.guild.default_role, overwrite=None)
        await interaction.followup.send("🔄 Đã khôi phục cài đặt gốc phòng!", ephemeral=True)

    @discord.ui.button(label="Nhận chủ", style=discord.ButtonStyle.secondary, emoji="👑", row=3, custom_id="vc_claim")
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel: return
        channel = interaction.user.voice.channel
        room = get_room(channel.id)
        if not room: return await interaction.response.send_message("❌ Không phải phòng tạm!", ephemeral=True)
        owner = interaction.guild.get_member(room["owner_id"])
        if owner and owner in channel.members: return await interaction.response.send_message("❌ Chủ cũ vẫn đang ở trong phòng!", ephemeral=True)
        save_room(interaction.guild.id, channel.id, interaction.user.id, channel.category_id if channel.category else 0)
        await send_blog_log(interaction.guild, "👑 NHẬN CHỦ PHÒNG", f"Phòng: {channel.mention} | Chủ mới: {interaction.user.mention}", discord.Color.purple())
        await interaction.response.send_message(f"👑 **{interaction.user.display_name}** đã tiếp quản quyền chủ phòng!", ephemeral=True)

    @discord.ui.button(label="Chuyển chủ", style=discord.ButtonStyle.secondary, emoji="📤", row=3, custom_id="vc_transfer")
    async def transfer_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TransferModal())

# -----------------------------
# Bot Logic & Main Event Handlers
# -----------------------------
class VoiceChatBot(commands.Bot):
    def __init__(self): super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(VoiceControlView())
        await self.tree.sync()
        self.loop.create_task(start_web_server())
        log.info("Bot đã đồng bộ hoàn tất!")

bot = VoiceChatBot()

async def create_room(guild: discord.Guild, member: discord.Member, category: discord.CategoryChannel):
    existing = get_owned_room(guild.id, member.id)
    if existing:
        old = guild.get_channel(existing["channel_id"])
        if isinstance(old, discord.VoiceChannel):
            try:
                await member.move_to(old)
                await send_blog_log(guild, "🔁 TÁI SỬ DỤNG PHÒNG", f"Thành viên: {member.mention} | Phòng: {old.mention}", discord.Color.dark_grey())
                return old
            except discord.HTTPException: pass
        delete_room_record(existing["channel_id"])

    new_channel = await guild.create_voice_channel(name=f"{ROOM_PREFIX} Phòng của {member.display_name}", category=category)
    await member.move_to(new_channel)
    save_room(guild.id, new_channel.id, member.id, category.id)
    await send_blog_log(guild, "✨ TẠO PHÒNG MỚI", f"Chủ phòng: {member.mention} | Phòng: {new_channel.mention}", discord.Color.green())

    embed = discord.Embed(title="🎛️ BẢNG ĐIỀU KHIỂN PHÒNG", description=f"Chủ phòng: {member.mention}\n\nDùng các nút bên dưới để tùy chỉnh không gian.", color=discord.Color.blurple())
    embed.set_thumbnail(url=member.display_avatar.url)
    await new_channel.send(content=f"👋 Chào mừng {member.mention}!", embed=embed, view=VoiceControlView())
    return new_channel

@bot.event
async def on_ready():
    init_db()
    log.info("Đăng nhập thành công bot: %s", bot.user)

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if after.channel and isinstance(after.channel, discord.VoiceChannel):
        if after.channel.name == DEFAULT_GENERATOR and after.channel.category:
            await create_room(member.guild, member, after.channel.category)

    if before.channel and isinstance(before.channel, discord.VoiceChannel):
        if before.channel.name == DEFAULT_GENERATOR: return
        room = get_room(before.channel.id)
        if room and len(before.channel.members) == 0:
            rid, rname = before.channel.id, before.channel.name
            delete_room_record(rid)
            try:
                await before.channel.delete()
                await send_blog_log(member.guild, "🗑️ XÓA PHÒNG TRỐNG", f"Phòng: `{rname}`", discord.Color.red())
            except discord.HTTPException: pass

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    
    # 1. Log tin nhắn phòng thoại tạm
    room = get_room(message.channel.id)
    if room:
        await send_blog_log(
            message.guild,
            "💬 NHẬT KÝ PHÒNG TẠM",
            f"Người gửi: {message.author.mention} | Phòng: {message.channel.mention} | Nội dung: {message.content or '[Tệp đính kèm]'}",
            discord.Color.light_embed()
        )
    
    # 2. Theo dõi thời gian thực kênh chat được gán bằng lệnh /track-channel
    gen = get_generator(message.guild.id)
    if gen and gen["tracked_text_channel_id"] and message.channel.id == gen["tracked_text_channel_id"]:
        await send_blog_log(
            message.guild,
            "⚡ THEO DÕI KÊNH CHAT CHUNG",
            f"Người gửi: {message.author.mention} | Kênh: {message.channel.mention} | Nội dung: {message.content or '[Tệp đính kèm]'}",
            discord.Color.blue()
        )

    await bot.process_commands(message)

# -----------------------------
# Slash Commands
# -----------------------------

@bot.tree.command(name="setup", description="[Admin] Khởi tạo hệ thống phòng thoại và kênh blog tập trung")
@app_commands.checks.has_permissions(administrator=True)
async def setup_cmd(interaction: discord.Interaction):
    guild = interaction.guild
    if not interaction.channel or not interaction.channel.category:
        return await interaction.response.send_message("❌ Vui lòng dùng lệnh bên trong một kênh thuộc danh mục muốn cài đặt!", ephemeral=True)
    
    category = interaction.channel.category
    generator = discord.utils.get(category.voice_channels, name=DEFAULT_GENERATOR)
    if not generator: generator = await guild.create_voice_channel(DEFAULT_GENERATOR, category=category)

    blog_channel = discord.utils.get(category.text_channels, name=FIXED_BLOG_NAME)
    if not blog_channel: blog_channel = await guild.create_text_channel(FIXED_BLOG_NAME, category=category)

    save_generator(guild.id, category.id, generator.id, blog_channel.id)
    await interaction.response.send_message(f"✅ Khởi tạo hệ thống thành công tại danh mục **{category.name}**!", ephemeral=True)

@bot.tree.command(name="track-channel", description="[Admin] Tự động gán kênh chat hiện tại để bot theo dõi thời gian thực vào blog")
@app_commands.checks.has_permissions(administrator=True)
async def track_channel_cmd(interaction: discord.Interaction):
    guild = interaction.guild
    current_channel = interaction.channel
    
    if not isinstance(current_channel, discord.TextChannel):
        return await interaction.response.send_message("❌ Lệnh này chỉ có thể sử dụng bên trong một kênh văn bản!", ephemeral=True)

    update_tracked_channel(guild.id, current_channel.id)
    await send_blog_log(
        guild,
        "📌 ĐÃ GÁN KÊNH THEO DÕI MỚI",
        f"Người gán: {interaction.user.mention} | Kênh theo dõi: {current_channel.mention}",
        discord.Color.teal()
    )
    await interaction.response.send_message(f"✅ Đã tự động nhận diện và gán kênh {current_channel.mention} vào hệ thống theo dõi thời gian thực!", ephemeral=True)

@bot.tree.command(name="untrack-channel", description="[Admin] Hủy theo dõi và gỡ bỏ kênh chat đang được gán thời gian thực")
@app_commands.checks.has_permissions(administrator=True)
async def untrack_channel_cmd(interaction: discord.Interaction):
    guild = interaction.guild
    clear_tracked_channel(guild.id)
    await send_blog_log(
        guild,
        "🗑️ ĐÃ HỦY THEO DÕI KÊNH",
        f"Người hủy: {interaction.user.mention}",
        discord.Color.orange()
    )
    await interaction.response.send_message("✅ Đã hủy theo dõi kênh chat thành công!", ephemeral=True)

@bot.tree.command(name="room-allow", description="[Chủ phòng] Cho phép thành viên tham gia phòng thoại")
async def room_allow(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
    channel = interaction.user.voice.channel
    room = get_room(channel.id)
    if not room or room["owner_id"] != interaction.user.id:
        return await interaction.response.send_message("❌ Chỉ có chủ phòng mới có quyền sử dụng lệnh này!", ephemeral=True)
    
    await channel.set_permissions(user, connect=True, view_channel=True)
    await interaction.response.send_message(f"✅ Đã cấp quyền cho {user.mention}.", ephemeral=True)

@bot.tree.command(name="room-deny", description="[Chủ phòng] Cấm thành viên tham gia phòng thoại")
async def room_deny(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
    channel = interaction.user.voice.channel
    room = get_room(channel.id)
    if not room or room["owner_id"] != interaction.user.id:
        return await interaction.response.send_message("❌ Chỉ có chủ phòng mới có quyền sử dụng lệnh này!", ephemeral=True)
    
    await channel.set_permissions(user, connect=False)
    if user.voice and user.voice.channel == channel: await user.move_to(None)
    await interaction.response.send_message(f"🚫 Đã cấm {user.mention}.", ephemeral=True)

@bot.tree.command(name="room-kick", description="[Chủ phòng] Đuổi thành viên ra khỏi phòng thoại")
async def room_kick(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm!", ephemeral=True)
    channel = interaction.user.voice.channel
    room = get_room(channel.id)
    if not room or room["owner_id"] != interaction.user.id:
        return await interaction.response.send_message("❌ Chỉ có chủ phòng mới có quyền sử dụng lệnh này!", ephemeral=True)
    
    if user.voice and user.voice.channel == channel:
        await user.move_to(None)
        await interaction.response.send_message(f"👞 Đã đá {user.mention} ra khỏi phòng.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Thành viên không ở trong phòng của bạn.", ephemeral=True)

@setup_cmd.error
@track_channel_cmd.error
@untrack_channel_cmd.error
async def admin_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("❌ Bạn không có quyền sử dụng lệnh này (Yêu cầu quyền Quản trị viên).", ephemeral=True)

if __name__ == "__main__":
    init_db()
    bot.run(TOKEN)
