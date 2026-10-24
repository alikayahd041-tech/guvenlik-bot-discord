import os
import re
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DİSCORD_TOKEN", "").strip()
ROBLOX_API_KEY = os.getenv("ROBLOX_API_KEY", "").strip()
ROBLOX_GROUP_ID = os.getenv("ROBLOX_GROUP_ID", "").strip()
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0") or 0)
AUTO_REPLY_FILE = "auto_replies.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("discord-roblox-bot")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)

http_session: Optional[aiohttp.ClientSession] = None
auto_replies = {}
voice_clients = {}

def load_auto_replies():
    global auto_replies
    try:
        with open(AUTO_REPLY_FILE, "r", encoding="utf-8") as f:
            auto_replies = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        auto_replies = {}

def save_auto_replies():
    with open(AUTO_REPLY_FILE, "w", encoding="utf-8") as f:
        json.dump(auto_replies, f, ensure_ascii=False, indent=2)

def is_configured(value: str) -> bool:
    return bool(value and not value.startswith("BURAYA_"))

async def send_log(guild: discord.Guild, embed: discord.Embed):
    channel = guild.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None
    if channel is None:
        return
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        log.exception("Log kanalına mesaj gönderilemedi.")

def log_embed(title: str, actor: discord.abc.User, description: str, color=discord.Color.blurple()):
    embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="İşlemi yapan", value=f"{actor.mention} (`{actor.id}`)", inline=False)
    return embed

async def require_moderator(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Bu komut yalnızca sunucuda kullanılabilir.", ephemeral=True)
        return False
    if not interaction.user.guild_permissions.moderate_members and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Bu komut için `Üyeleri Zaman Aşımına Uğrat` veya Yönetici yetkisi gerekiyor.", ephemeral=True)
        return False
    return True

async def roblox_request(method: str, url: str, **kwargs):
    if not is_configured(ROBLOX_API_KEY):
        raise RuntimeError("ROBLOX_API_KEY .env dosyasında ayarlanmamış.")
    headers = kwargs.pop("headers", {})
    headers["x-api-key"] = ROBLOX_API_KEY
    headers.setdefault("Content-Type", "application/json")
    async with http_session.request(method, url, headers=headers, **kwargs) as response:
        body = await response.text()
        if response.status >= 400:
            raise RuntimeError(f"Roblox API hata kodu {response.status}: {body[:700]}")
        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw": body}

async def roblox_user_id(username: str) -> tuple[str, str]:
    data = await roblox_request(
        "POST", "https://users.roblox.com/v1/usernames/users",
        json={"usernames": [username], "excludeBannedUsers": False},
        headers={}
    )
    users = data.get("data", [])
    if not users:
        raise RuntimeError(f"`{username}` adlı Roblox kullanıcısı bulunamadı.")
    return str(users[0]["id"]), users[0].get("name", username)

async def get_group_roles():
    if not ROBLOX_GROUP_ID.isdigit():
        raise RuntimeError("ROBLOX_GROUP_ID .env dosyasında sayısal bir grup ID'si olmalı.")
    data = await roblox_request("GET", f"https://apis.roblox.com/cloud/v2/groups/{ROBLOX_GROUP_ID}/roles")
    return data.get("groupRoles", data.get("roles", []))

async def find_membership_id(user_id: str) -> str:
    # Cloud API filter lets us look up a membership for one user; follow pagination if needed.
    url = f"https://apis.roblox.com/cloud/v2/groups/{ROBLOX_GROUP_ID}/memberships"
    params = {"filter": f"user==users/{user_id}", "maxPageSize": "100"}
    data = await roblox_request("GET", url, params=params)
    memberships = data.get("groupMemberships", data.get("memberships", []))
    for membership in memberships:
        user = membership.get("user", "")
        if isinstance(user, dict):
            user = user.get("path", user.get("name", ""))
        if str(user).endswith(f"/{user_id}") or str(user) == f"users/{user_id}":
            return str(membership.get("path", "")).rsplit("/", 1)[-1] or str(membership.get("membershipId", ""))
    # If filter behavior differs, walk nextPageToken pages and compare user paths.
    token = data.get("nextPageToken")
    while token:
        data = await roblox_request("GET", url, params={"pageToken": token, "maxPageSize": "100"})
        memberships = data.get("groupMemberships", data.get("memberships", []))
        for membership in memberships:
            user = membership.get("user", "")
            if isinstance(user, dict):
                user = user.get("path", user.get("name", ""))
            if str(user).endswith(f"/{user_id}") or str(user) == f"users/{user_id}":
                return str(membership.get("path", "")).rsplit("/", 1)[-1] or str(membership.get("membershipId", ""))
        token = data.get("nextPageToken")
    raise RuntimeError("Bu kullanıcı Roblox topluluğunda bulunamadı veya üyelik bilgisi API tarafından döndürülmedi.")

async def assign_roblox_role(username: str, role_name: str):
    user_id, canonical_name = await roblox_user_id(username)
    roles = await get_group_roles()
    wanted = role_name.casefold().strip()
    matches = []
    for role in roles:
        name = role.get("displayName", role.get("name", ""))
        if name.casefold() == wanted:
            matches.append(role)
    if not matches:
        available = [r.get("displayName", r.get("name", "?")) for r in roles]
        raise RuntimeError("Bu isimde rütbe bulunamadı. Mevcut rütbeler: " + ", ".join(available[:25]))
    role = matches[0]
    role_path = role.get("path", "")
    role_id = role_path.rsplit("/", 1)[-1] if role_path else str(role.get("id", ""))
    if not role_id:
        raise RuntimeError("Roblox API yanıtında rütbe ID'si bulunamadı.")
    membership_id = await find_membership_id(user_id)
    url = f"https://apis.roblox.com/cloud/v2/groups/{ROBLOX_GROUP_ID}/memberships/{membership_id}:assignRole"
    await roblox_request("POST", url, json={"role": f"groups/{ROBLOX_GROUP_ID}/roles/{role_id}"})
    return canonical_name, role.get("displayName", role_name)

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        log.info("Bot giriş yaptı: %s | %s slash komutu", bot.user, len(synced))
    except Exception:
        log.exception("Slash komutları senkronize edilemedi.")
    log.info("Hazır: %s", bot.user)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    content = message.content.casefold()
    for trigger, answer in list(auto_replies.items()):
        if trigger.casefold() in content:
            try:
                await message.channel.send(answer)
            except discord.HTTPException:
                pass
            break
    await bot.process_commands(message)

@bot.tree.command(name="duyuru", description="Başlıklı duyuru gönderir ve isteğe bağlı rol etiketler.")
@app_commands.describe(baslik="Duyuru başlığı", mesaj="Duyuru içeriği", etiket="Etiketlenecek rol (isteğe bağlı)")
async def duyuru(interaction: discord.Interaction, baslik: str, mesaj: str, etiket: Optional[discord.Role] = None):
    if not await require_moderator(interaction): return
    embed = discord.Embed(title=baslik[:256], description=mesaj[:4000], color=discord.Color.gold(), timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=f"Duyuru • {interaction.user}")
    content = etiket.mention if etiket else None
    await interaction.channel.send(content=content, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False))
    await interaction.response.send_message("Duyuru gönderildi.", ephemeral=True)
    await send_log(interaction.guild, log_embed("Duyuru gönderildi", interaction.user, f"**Başlık:** {baslik}\n**Kanal:** {interaction.channel.mention}"))

@bot.tree.command(name="otomatik-cevap", description="Bir kelime veya ifadeye otomatik cevap ekler.")
@app_commands.describe(tetikleyici="Mesajlarda aranacak kelime/ifade", cevap="Botun vereceği cevap")
async def otomatik_cevap(interaction: discord.Interaction, tetikleyici: str, cevap: str):
    if not await require_moderator(interaction): return
    if len(tetikleyici) > 100 or len(cevap) > 1500:
        await interaction.response.send_message("Tetikleyici en fazla 100, cevap en fazla 1500 karakter olabilir.", ephemeral=True); return
    auto_replies[tetikleyici] = cevap
    save_auto_replies()
    await interaction.response.send_message(f"'{tetikleyici}' tetikleyicisi kaydedildi.", ephemeral=True)
    await send_log(interaction.guild, log_embed("Otomatik cevap eklendi", interaction.user, f"**Tetikleyici:** {tetikleyici}\n**Cevap:** {cevap[:800]}"))

@bot.tree.command(name="tam-yasak-at", description="Bir üyeyi sunucudan yasaklar ve kanıtı kaydeder.")
@app_commands.describe(kisi="Yasaklanacak Discord üyesi", sebep="Yasaklama sebebi", kanit="Kanıt bağlantısı (isteğe bağlı)")
async def tam_yasak_at(interaction: discord.Interaction, kisi: discord.Member, sebep: str, kanit: Optional[str] = None):
    if not await require_moderator(interaction): return
    if kisi.id == interaction.user.id:
        await interaction.response.send_message("Kendini yasaklayamazsın.", ephemeral=True); return
    if kisi.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        await interaction.response.send_message("Bu üyenin rolü senin rolüne eşit veya daha yüksek.", ephemeral=True); return
    await kisi.ban(reason=f"{sebep} | Yetkili: {interaction.user} ({interaction.user.id})", delete_message_seconds=0)
    await interaction.response.send_message(f"{kisi} sunucudan yasaklandı.", ephemeral=True)
    desc = f"**Kişi:** {kisi} (`{kisi.id}`)\n**Sebep:** {sebep}\n**Kanıt:** {kanit or 'Belirtilmedi'}"
    await send_log(interaction.guild, log_embed("Üye yasaklandı", interaction.user, desc, discord.Color.red()))

@bot.tree.command(name="kick", description="Bir üyeyi sunucudan çıkarır ve kanıtı kaydeder.")
@app_commands.describe(kisi="Çıkarılacak Discord üyesi", sebep="Çıkarma sebebi", kanit="Kanıt bağlantısı (isteğe bağlı)")
async def kick(interaction: discord.Interaction, kisi: discord.Member, sebep: str, kanit: Optional[str] = None):
    if not await require_moderator(interaction): return
    if kisi.id == interaction.user.id:
        await interaction.response.send_message("Kendini sunucudan çıkaramazsın.", ephemeral=True); return
    if kisi.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        await interaction.response.send_message("Bu üyenin rolü senin rolüne eşit veya daha yüksek.", ephemeral=True); return
    await kisi.kick(reason=f"{sebep} | Yetkili: {interaction.user} ({interaction.user.id})")
    await interaction.response.send_message(f"{kisi} sunucudan çıkarıldı.", ephemeral=True)
    desc = f"**Kişi:** {kisi} (`{kisi.id}`)\n**Sebep:** {sebep}\n**Kanıt:** {kanit or 'Belirtilmedi'}"
    await send_log(interaction.guild, log_embed("Üye sunucudan çıkarıldı", interaction.user, desc, discord.Color.orange()))

@bot.tree.command(name="ses-gir", description="Botu seçilen ses kanalına sokar.")
@app_commands.describe(kanal="Katılınacak ses kanalı")
async def ses_gir(interaction: discord.Interaction, kanal: discord.VoiceChannel):
    if not interaction.guild:
        await interaction.response.send_message("Bu komut sunucuda kullanılabilir.", ephemeral=True); return
    if not interaction.user.guild_permissions.mute_members and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Bu komut için üyeleri susturma veya Yönetici yetkisi gerekiyor.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    try:
        voice = interaction.guild.voice_client
        if voice and voice.is_connected():
            await voice.move_to(kanal)
        else:
            voice = await kanal.connect()
        voice_clients[interaction.guild.id] = voice
        await interaction.followup.send(f"{kanal.mention} kanalına katıldım. Müzik çalmak için `/muzik` komutunu kullanabilirsin.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Ses kanalına katılamadım: {e}", ephemeral=True)

@bot.tree.command(name="ses-cik", description="Botu ses kanalından çıkarır.")
async def ses_cik(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Bu komut sunucuda kullanılabilir.", ephemeral=True); return
    if not interaction.user.guild_permissions.mute_members and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Bu komut için üyeleri susturma veya Yönetici yetkisi gerekiyor.", ephemeral=True); return
    voice = interaction.guild.voice_client
    if voice and voice.is_connected():
        await voice.disconnect()
        voice_clients.pop(interaction.guild.id, None)
        await interaction.response.send_message("Ses kanalından ayrıldım.", ephemeral=True)
    else:
        await interaction.response.send_message("Şu anda bir ses kanalında değilim.", ephemeral=True)

@bot.tree.command(name="muzik", description="Bir ses kanalında doğrudan ses URL'sinden müzik çalar (FFmpeg gerekli).")
@app_commands.describe(url="Desteklenen bir ses akışının URL'si")
async def muzik(interaction: discord.Interaction, url: str):
    if not interaction.guild or not interaction.guild.voice_client:
        await interaction.response.send_message("Önce `/ses-gir` komutuyla ses kanalına katılmalıyım.", ephemeral=True); return
    if not interaction.user.guild_permissions.mute_members and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Bu komut için üyeleri susturma veya Yönetici yetkisi gerekiyor.", ephemeral=True); return
    if not url.startswith(("https://", "http://")):
        await interaction.response.send_message("Geçerli bir http(s) URL'si gir.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    try:
        voice = interaction.guild.voice_client
        if voice.is_playing(): voice.stop()
        source = await discord.FFmpegOpusAudio.from_probe(url, method="fallback")
        voice.play(source)
        await interaction.followup.send("Ses akışı başlatıldı. URL'nin doğrudan ses akışı olması gerekir.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Müzik başlatılamadı. FFmpeg kurulu mu ve URL doğrudan ses akışı mı? Hata: {e}", ephemeral=True)

@bot.tree.command(name="rütbe-degistir", description="Roblox topluluğunda bir üyenin rütbesini değiştirir.")
@app_commands.describe(roblox_isim="Roblox kullanıcı adı", rutbe="Roblox topluluğundaki rütbenin tam adı", sebep="Rütbe değişikliği sebebi")
async def rutbe_degistir(interaction: discord.Interaction, roblox_isim: str, rutbe: str, sebep: str):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Bu komut yalnızca sunucuda kullanılabilir.", ephemeral=True); return
    if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_roles):
        await interaction.response.send_message("Bu komut için Rolleri Yönet veya Yönetici yetkisi gerekiyor.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    try:
        canonical_name, role_name = await assign_roblox_role(roblox_isim, rutbe)
        await interaction.followup.send(f"✅ **{canonical_name}** kullanıcısının rütbesi **{role_name}** olarak ayarlandı.", ephemeral=True)
        desc = f"**Roblox kullanıcısı:** [{canonical_name}](https://www.roblox.com/users/{await roblox_user_id(canonical_name)}/profile)\n**Yeni rütbe:** {role_name}\n**Sebep:** {sebep}\n**Group ID:** {ROBLOX_GROUP_ID}"
        await send_log(interaction.guild, log_embed("Roblox rütbesi değiştirildi", interaction.user, desc, discord.Color.green()))
    except Exception as e:
        log.exception("Roblox rütbe değişikliği başarısız")
        await interaction.followup.send(f"❌ Rütbe değiştirilemedi: {e}", ephemeral=True)
        await send_log(interaction.guild, log_embed("Roblox rütbe değişikliği başarısız", interaction.user, f"**Kullanıcı:** {roblox_isim}\n**İstenen rütbe:** {rutbe}\n**Sebep:** {sebep}\n**Hata:** {str(e)[:700]}", discord.Color.red()))

@bot.tree.command(name="rütbeler", description="Roblox topluluğundaki mevcut rütbeleri listeler.")
async def rutbeler(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        roles = await get_group_roles()
        names = [r.get("displayName", r.get("name", "?")) for r in roles]
        await interaction.followup.send("**Roblox rütbeleri:**\n" + "\n".join(f"• {n}" for n in names[:50]), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Rütbeler alınamadı: {e}", ephemeral=True)

async def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN .env dosyasında eksik. .env.example dosyasını .env olarak kopyalayıp doldur.")
    load_auto_replies()
    global http_session
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        http_session = session
        async with bot:
            await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
                                                                                            
