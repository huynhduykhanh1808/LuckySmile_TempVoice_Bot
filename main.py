import os
import sqlite3
import logging
from typing import Optional, Union

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "tempvoice.db")
ROOM_PREFIX = os.getenv("ROOM_PREFIX", "🔊")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("TempVoiceBot")

# Bật đầy đủ các Intents cần thiết
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True
intents.message_content = True

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

def save_generator(guild_id: int, category_id: int, generator_id: int, control_id: Optional[int] = None):
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

def update_room_owner(channel_id: int, new_owner_id: int):
    with db() as con:
        con.execute("UPDATE rooms SET owner_id = ? WHERE channel_id = ?", (new_owner_id, channel_id))

def delete_room_record(channel_id: int):
    with db() as con:
        con.execute("DELETE FROM rooms WHERE channel_id = ?", (channel_id,))

# -----------------------------
# HELPER FUNCTIONS
# -----------------------------
async def is_room_owner_or_admin(interaction: discord.Interaction) -> bool:
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("❌ Bạn phải đang ở trong phòng thoại tạm thời!", ephemeral=True)
        return False
    
    ch = interaction.user.voice.channel
    room = get_room(ch.id)
    if not room:
        await interaction.response.send_message("❌ Đây không phải là phòng thoại tạm thời!", ephemeral=True)
        return False

    if room["owner_id"] == interaction.user.id or interaction.user.guild_permissions.administrator:
        return True
    else:
        await interaction.response.send_message("❌ Chỉ chủ sở hữu phòng mới có quyền thao tác!", ephemeral=True)
        return False

# -----------------------------
# MODAL & SELECT UI
# -----------------------------
class LimitModal(discord.ui.Modal, title="Cài đặt giới hạn người dùng"):
    limit = discord.ui.TextInput(label="Số người tối đa (0 = Không giới hạn)", placeholder="Nhập từ 0 đến 99...", min_length=1, max_length=2)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = int(self.limit.value)
            if not (0 <= val <= 99): raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ Số người nhập vào không hợp lệ (từ 0 đến 99)!", ephemeral=True)

        await interaction.user.voice.channel.edit(user_limit=val)
        await interaction.response.send_message(f"✅ Đã cập nhật giới hạn phòng thành **{val}** người!", ephemeral=True)

class RenameModal(discord.ui.Modal, title="Đổi tên phòng thoại"):
    new_name = discord.ui.TextInput(label="Tên phòng mới", placeholder="Nhập tên phòng mong muốn...", min_length=1, max_length=50)

    async def on_submit(self, interaction: discord.Interaction):
        ch = interaction.user.voice.channel
        await ch.edit(name=f"{ROOM_PREFIX} {self.new_name.value}")
        await interaction.response.send_message(f"✅ Đã đổi tên phòng thành: **{self.new_name.value}**", ephemeral=True)

class RegionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Tự động (Auto)", value="auto", description="Để Discord chọn máy chủ tốt nhất"),
            discord.SelectOption(label="Singapore", value="singapore", description="Độ trễ thấp cho Đông Nam Á"),
            discord.SelectOption(label="Hong Kong", value="hongkong"),
            discord.SelectOption(label="Japan", value="japan"),
            discord.SelectOption(label="US Central", value="us-central"),
            discord.SelectOption(label="Rotterdam", value="rotterdam")
        ]
        super().__init__(placeholder="🌐 Chọn khu vực máy chủ Voice...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        region_val = None if self.values[0] == "auto" else self.values[0]
        await interaction.user.voice.channel.edit(rtc_region=region_val)
        await interaction.response.send_message(f"🌐 Đã đổi khu vực máy chủ sang: **{self.values[0].upper()}**", ephemeral=True)

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
        if not await is_room_owner_or_admin(interaction): return
        ch = interaction.user.voice.channel
        await ch.set_permissions(interaction.guild.default_role, connect=False)
        await ch.set_permissions(interaction.user, connect=True)
        await interaction.response.send_message("🔒 Đã khóa phòng thoại! Chỉ những người được cho phép mới có thể vào.", ephemeral=True)

    @discord.ui.button(label="Mở khóa", style=discord.ButtonStyle.secondary, emoji="🔓", row=0, custom_id="vc_unlock")
    async def unlock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await is_room_owner_or_admin(interaction): return
        ch = interaction.user.voice.channel
        await ch.set_permissions(interaction.guild.default_role, connect=None)
        await interaction.response.send_message("🔓 Đã mở khóa phòng thoại!", ephemeral=True)

    @discord.ui.button(label="Ẩn phòng", style=discord.ButtonStyle.secondary, emoji="🥷", row=0, custom_id="vc_hide")
    async def hide_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await is_room_owner_or_admin(interaction): return
        ch = interaction.user.voice.channel
        await ch.set_permissions(interaction.guild.default_role, view_channel=False)
        await interaction.response.send_message("🥷 Đã ẩn phòng thoại khỏi danh sách kênh!", ephemeral=True)

    @discord.ui.button(label="Hiện phòng", style=discord.ButtonStyle.secondary, emoji="👁️", row=0, custom_id="vc_unhide")
    async def unhide_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await is_room_owner_or_admin(interaction): return
        ch = interaction.user.voice.channel
        await ch.set_permissions(interaction.guild.default_role, view_channel=None)
        await interaction.response.send_message("👁️ Phòng thoại hiện đã công khai!", ephemeral=True)

    @discord.ui.button(label="Nhận quyền", style=discord.ButtonStyle.success, emoji="👑", row=0, custom_id="vc_claim")
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Bạn phải ở trong phòng thoại!", ephemeral=True)
        
        ch = interaction.user.voice.channel
        room = get_room(ch.id)
        if not room:
            return await interaction.response.send_message("❌ Đây không phải phòng tạm thời!", ephemeral=True)

        owner_member = interaction.guild.get_member(room["owner_id"])
        if owner_member and owner_member in ch.members and owner_member.id != interaction.user.id:
            return await interaction.response.send_message(f"❌ Chủ phòng ({owner_member.mention}) vẫn đang ở trong phòng này!", ephemeral=True)

        update_room_owner(ch.id, interaction.user.id)
        await ch.set_permissions(interaction.user, manage_channels=True, move_members=True, connect=True)
        await interaction.response.send_message(f"👑 Bạn đã trở thành chủ phòng mới của **{ch.name}**!", ephemeral=False)

    @discord.ui.button(label="Đổi tên", style=discord.ButtonStyle.primary, emoji="✏️", row=1, custom_id="vc_rename")
    async def rename_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await is_room_owner_or_admin(interaction): return
        await interaction.response.send_modal(RenameModal())

    @discord.ui.button(label="Giới hạn", style=discord.ButtonStyle.primary, emoji="👥", row=1, custom_id="vc_limit")
    async def limit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await is_room_owner_or_admin(interaction): return
        await interaction.response.send_modal(LimitModal())

    @discord.ui.button(label="Khu vực", style=discord.ButtonStyle.primary, emoji="🌐", row=1, custom_id="vc_region")
    async def region_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await is_room_owner_or_admin(interaction): return
        await interaction.response.send_message("🌐 Chọn khu vực máy chủ muốn chuyển đổi:", view=RegionView(), ephemeral=True)

    @discord.ui.button(label="Đặt lại", style=discord.ButtonStyle.danger, emoji="🔄", row=1, custom_id="vc_reset")
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await is_room_owner_or_admin(interaction): return
        ch = interaction.user.voice.channel
        await ch.edit(name=f"{ROOM_PREFIX} Phòng của {interaction.user.display_name}", user_limit=0, rtc_region=None)
        await ch.set_permissions(interaction.guild.default_role, overwrite=None)
        await interaction.response.send_message("🔄 Đã khôi phục mặc định phòng thoại!", ephemeral=True)

# -----------------------------
# LỆNH SLASH ADVANCED
# -----------------------------
@bot.tree.command(name="setup-voice", description="[Admin] Tạo hệ thống tạo phòng thoại tự động")
@app_commands.checks.has_permissions(administrator=True)
async def setup_voice(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    # 1. Tạo Category
    category = await guild.create_category("🎙️・LUCKY VOICE ROOMS")
    
    # 2. Tạo Kênh Tạo Phòng
    generator = await guild.create_voice_channel("➕・Tạo Phòng Ngay", category=category)
    
    # 3. Tạo Kênh Điều Khiển
    control = await guild.create_text_channel("🎛️・quản-lý-phòng", category=category)

    save_generator(guild.id, category.id, generator.id, control.id)

    embed = discord.Embed(
        title="🎛️ BẢNG ĐIỀU KHIỂN PHÒNG THOẠI TẠM THỜI",
        description=(
            "Tham gia kênh **➕・Tạo Phòng Ngay** để tự động sở hữu một phòng thoại riêng!\n\n"
            "**Hướng dẫn các nút:**\n"
            "🔒 `Khóa` - Cấm người lạ tham gia\n"
            "🔓 `Mở khóa` - Cho phép mọi người vào phòng\n"
            "🥷 `Ẩn phòng` - Giấu phòng khỏi danh sách\n"
            "👁️ `Hiện phòng` - Công khai phòng thoại\n"
            "👑 `Nhận quyền` - Nhận chủ phòng nếu chủ cũ đã thoát\n"
            "✏️ `Đổi tên` - Thay đổi tên phòng thoại\n"
            "👥 `Giới hạn` - Giới hạn số lượng thành viên\n"
            "🌐 `Khu vực` - Đổi Region máy chủ giảm ping\n"
            "🔄 `Đặt lại` - Khôi phục cài đặt gốc"
        ),
        color=discord.Color.gold()
    )
    embed.set_footer(text="Lucky TempVoice Bot • Hệ thống tự động")
    await control.send(embed=embed, view=VoiceControlView())

    await interaction.followup.send("✅ Đã khởi tạo thành công danh mục và kênh tạo phòng voice!", ephemeral=True)

@bot.tree.command(name="room-allow", description="Cho phép một thành viên cụ thể truy cập phòng thoại của bạn")
async def room_allow(interaction: discord.Interaction, user: discord.Member):
    if not await is_room_owner_or_admin(interaction): return
    ch = interaction.user.voice.channel
    await ch.set_permissions(user, connect=True, view_channel=True)
    await interaction.response.send_message(f"✅ Đã cấp quyền truy cập cho {user.mention}!", ephemeral=True)

@bot.tree.command(name="room-deny", description="Cấm thành viên truy cập và đuổi khỏi phòng")
async def room_deny(interaction: discord.Interaction, user: discord.Member):
    if not await is_room_owner_or_admin(interaction): return
    ch = interaction.user.voice.channel
    await ch.set_permissions(user, connect=False)
    if user.voice and user.voice.channel == ch:
        await user.move_to(None)
    await interaction.response.send_message(f"🚫 Đã cấm và chặn {user.mention} khỏi phòng!", ephemeral=True)

@bot.tree.command(name="room-kick", description="Đuổi thành viên ra khỏi phòng hiện tại")
async def room_kick(interaction: discord.Interaction, user: discord.Member):
    if not await is_room_owner_or_admin(interaction): return
    ch = interaction.user.voice.channel
    if user.voice and user.voice.channel == ch:
        await user.move_to(None)
        await interaction.response.send_message(f"👞 Đã đuổi thành công {user.mention} ra khỏi phòng!", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Thành viên {user.mention} hiện không ở trong phòng của bạn!", ephemeral=True)

# -----------------------------
# SỰ KIỆN CHÍNH (EVENTS)
# -----------------------------
@bot.event
async def on_ready():
    init_db()
    bot.add_view(VoiceControlView())
    try:
        synced = await bot.tree.sync()
        log.info(f"Đã đồng bộ {len(synced)} lệnh Slash command.")
    except Exception as e:
        log.error(f"Lỗi đồng bộ lệnh: {e}")
    log.info(f"Bot đã sẵn sàng hoạt động với tên: {bot.user}")

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # 1. Người dùng tham gia Kênh Tạo Phòng
    if after.channel:
        row = get_generator(member.guild.id)
        if row and after.channel.id == row["generator_id"]:
            category = member.guild.get_channel(row["category_id"])
            
            # Tạo kênh thoại tạm thời mới
            new_channel = await member.guild.create_voice_channel(
                name=f"{ROOM_PREFIX} Phòng của {member.display_name}",
                category=category
            )
            # Phân quyền cho Chủ phòng
            await new_channel.set_permissions(member, manage_channels=True, move_members=True, connect=True)
            await member.move_to(new_channel)
            
            # Lưu dữ liệu phòng
            save_room(member.guild.id, new_channel.id, member.id, category.id)

            # Gửi tin nhắn chào mừng và Bảng điều khiển riêng trong phòng vừa tạo
            embed = discord.Embed(
                title="🎛️ QUẢN LÝ PHÒNG THOẠI",
                description=f"Chào mừng {member.mention}! Bạn hiện là chủ sở hữu của phòng thoại này.\nSử dụng các nút bên dưới để tùy chỉnh thiết lập phòng.",
                color=discord.Color.green()
            )
            await new_channel.send(content=f"{member.mention}", embed=embed, view=VoiceControlView())

    # 2. Xử lý khi thành viên thoát kênh hoặc kênh trống
    if before.channel:
        room_data = get_room(before.channel.id)
        if room_data:
            # Nếu không còn ai trong phòng -> Xóa phòng & Xóa dữ liệu DB
            if len(before.channel.members) == 0:
                try:
                    await before.channel.delete(reason="Phòng thoại không còn thành viên.")
                    delete_room_record(before.channel.id)
                except discord.HTTPException:
                    pass
            # Nếu Chủ phòng rời đi nhưng phòng vẫn còn người -> Tự động chuyển quyền cho người tiếp theo
            elif before.channel.members and member.id == room_data["owner_id"]:
                new_owner = before.channel.members[0]
                update_room_owner(before.channel.id, new_owner.id)
                await before.channel.set_permissions(new_owner, manage_channels=True, move_members=True, connect=True)
                await before.channel.send(f"👑 Chủ phòng đã rời đi. **{new_owner.mention}** đã được chỉ định làm chủ phòng mới!")

if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("Chưa cấu hình DISCORD_TOKEN!")
    bot.run(TOKEN)