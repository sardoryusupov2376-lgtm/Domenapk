"""Domen.uz: Telegram bot + Mustaqil Web/PWA Ilova + Download Landing serveri.
Ishga tushirish: python main.py   (index.html, download.html, manifest.json, sw.js shu papkada turadi)
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import platform
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qsl

import aiohttp
import dns.asyncresolver
import dns.resolver
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
                           MenuButtonWebApp, Message, WebAppInfo)
from aiohttp import web

BASE = Path(__file__).parent

# ---------- Sozlamalar: .env faylidan o'qiladi ----------
env_file = BASE / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip().rstrip("/")
CHANNEL = os.environ.get("CHANNEL", "@Yusupov_Dev1")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "Ilovachii")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "3000"))
ESKIZ_EMAIL = os.environ.get("ESKIZ_EMAIL", "")
ESKIZ_PASSWORD = os.environ.get("ESKIZ_PASSWORD", "")
CODE_VIA = os.environ.get("CODE_VIA", "screen")  # "screen" (saytda ekranda), "bot" yoki "sms"
ALLOW_WEB_ACCESS = os.environ.get("ALLOW_WEB_ACCESS", "1") == "1"
DB_PATH = os.environ.get("DB_PATH", str(BASE / "data.db"))
ZONES = {".uz": 150000, ".com": 180000, ".net": 200000, ".org": 190000, ".io": 520000}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("domen")
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

# ---------- Ma'lumotlar bazasi (SQLite) ----------
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, domain TEXT, years INT, total INT,
  phone TEXT, user_id INT, username TEXT, name TEXT, at TEXT);
""")


def kv_get(k):
    r = db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r[0] if r else None


def kv_set(k, v):
    db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, str(v)))
    db.commit()


bot: Bot = None
session: aiohttp.ClientSession = None
bot_username = ""
tasks: list = []
LOCAL_PORT = PORT
sub_cache: dict[int, float] = {}
codes: dict[str, dict] = {}
verified: dict[str, float] = {}
eskiz_token = None


# ---------- Telegram initData va Mustaqil Ilova (Web/PWA/APK) tekshiruvi ----------
def verify_init_data(raw: str):
    if not raw or not BOT_TOKEN:
        return None
    try:
        pairs = dict(parse_qsl(raw, keep_blank_values=True))
        h = pairs.pop("hash", None)
        if not h:
            return None
        key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()

        def ok(d):
            s = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
            return hmac.compare_digest(hmac.new(key, s.encode(), hashlib.sha256).hexdigest(), h)

        nosig = {k: v for k, v in pairs.items() if k != "signature"}
        if not (ok(pairs) or ok(nosig)):
            return None
        if time.time() - int(pairs.get("auth_date", 0)) > 86400:
            return None
        return json.loads(pairs["user"])
    except Exception:
        return None


async def is_sub(uid: int) -> bool:
    if uid <= 0 or not bot:
        return True
    if sub_cache.get(uid, 0) > time.time():
        return True
    try:
        m = await bot.get_chat_member(CHANNEL, uid)
    except Exception as e:
        log.warning("get_chat_member xato: %s | Bot kanalda admin ekanini tekshiring.", e)
        return False
    ok = m.status in ("creator", "administrator", "member") or (
        m.status == "restricted" and getattr(m, "is_member", False))
    if ok:
        sub_cache[uid] = time.time() + 60
    return ok


# ---------- Domen band/bo'shligi ----------
async def is_taken(name: str, zone: str) -> bool:
    d = name + zone
    if zone != ".uz" and session:
        try:
            async with session.get("https://rdap.org/domain/" + d,
                                   timeout=aiohttp.ClientTimeout(total=6)) as r:
                if r.status == 200:
                    return True
                if r.status == 404:
                    return False
        except Exception:
            pass
    try:
        await dns.asyncresolver.resolve(d, "NS", lifetime=5)
        return True
    except dns.resolver.NXDOMAIN:
        return False
    except dns.resolver.NoAnswer:
        return True


# ---------- SMS (Eskiz) ----------
async def eskiz_login():
    global eskiz_token
    fd = aiohttp.FormData()
    fd.add_field("email", ESKIZ_EMAIL)
    fd.add_field("password", ESKIZ_PASSWORD)
    async with session.post("https://notify.eskiz.uz/api/auth/login", data=fd) as r:
        j = await r.json()
    eskiz_token = (j.get("data") or {}).get("token")
    if not eskiz_token:
        raise RuntimeError(f"Eskiz login xato: {j}")


async def send_sms(phone: str, text: str) -> bool:
    if not ESKIZ_EMAIL:
        log.info("[SMS On-Screen] +%s: %s", phone, text)
        return True
    if not eskiz_token:
        await eskiz_login()
    for _ in range(2):
        fd = aiohttp.FormData()
        fd.add_field("mobile_phone", phone)
        fd.add_field("message", text)
        fd.add_field("from", "4546")
        async with session.post("https://notify.eskiz.uz/api/message/sms/send", data=fd,
                                headers={"Authorization": f"Bearer {eskiz_token}"}) as r:
            if r.status == 401:
                await eskiz_login()
                continue
            return r.status == 200
    return False


def norm_phone(p):
    d = re.sub(r"\D", "", str(p or ""))
    return d if re.fullmatch(r"998\d{9}", d) else None


# ---------- API ----------
routes = web.RouteTableDef()


def fail(cls, **kw):
    return cls(text='{"ok":false}', content_type="application/json", **kw)


async def auth(request, need_sub=True):
    raw = request.headers.get("X-Init-Data", "").strip()
    user = verify_init_data(raw)
    if user:
        if need_sub and not await is_sub(user["id"]):
            raise fail(web.HTTPForbidden)
        return user
    if ALLOW_WEB_ACCESS:
        return {"id": 0, "username": "", "first_name": "Ilova foydalanuvchisi", "standalone": True}
    raise fail(web.HTTPUnauthorized)


async def body_of(request):
    try:
        return await request.json()
    except Exception:
        return {}


@routes.get("/")
@routes.get("/index.html")
async def index(request):
    return web.FileResponse(BASE / "index.html", headers={"Cache-Control": "no-cache"})


@routes.get("/download")
@routes.get("/download.html")
async def download_page(request):
    target = BASE / "download.html"
    if target.exists():
        return web.FileResponse(target, headers={"Cache-Control": "no-cache"})
    return web.FileResponse(BASE / "index.html", headers={"Cache-Control": "no-cache"})


@routes.get("/manifest.json")
async def manifest_file(request):
    target = BASE / "manifest.json"
    if target.exists():
        return web.FileResponse(target, headers={"Content-Type": "application/manifest+json"})
    return web.json_response({
        "name": "Domen.uz — Milliy Domen Qidiruv va Buyurtma Ilovasi",
        "short_name": "Domen.uz",
        "start_url": "/?source=pwa",
        "display": "standalone",
        "background_color": "#052624",
        "theme_color": "#052624"
    })


@routes.get("/sw.js")
async def sw_file(request):
    target = BASE / "sw.js"
    if target.exists():
        return web.FileResponse(target, headers={"Content-Type": "application/javascript"})
    return web.Response(text="self.addEventListener('fetch', () => {});", content_type="application/javascript")



@routes.get("/download/Domen.uz.apk")
@routes.get("/Domen.uz.apk")
async def download_apk_file(request):
    apk_path = BASE / "Domen.uz.apk"
    if apk_path.exists():
        return web.FileResponse(
            apk_path,
            headers={
                "Content-Type": "application/vnd.android.package-archive",
                "Content-Disposition": 'attachment; filename="Domen.uz.apk"',
                "Cache-Control": "no-cache"
            }
        )
    raise web.HTTPFound("/download?apk=building")


@routes.get("/icon-192.png")
async def icon_192(request):
    p = BASE / "icon-192.png"
    if p.exists():
        return web.FileResponse(p, headers={"Content-Type": "image/png"})
    raise web.HTTPNotFound()


@routes.get("/icon-512.png")
async def icon_512(request):
    p = BASE / "icon-512.png"
    if p.exists():
        return web.FileResponse(p, headers={"Content-Type": "image/png"})
    raise web.HTTPNotFound()


@routes.get("/health")
async def health(request):
    return web.json_response({"ok": True, "bot": bot_username})


@routes.post("/api/sub-status")
async def api_sub(request):
    user = await auth(request, need_sub=False)
    if user.get("standalone"):
        return web.json_response({"ok": True, "standalone": True})
    return web.json_response({"ok": await is_sub(user["id"])})


@routes.post("/api/check-domain")
async def api_check(request):
    await auth(request)
    b = await body_of(request)
    name = str(b.get("name", "")).lower()
    if not NAME_RE.match(name):
        return web.json_response({"results": []})
    zones = [z for z in (b.get("zones") or []) if z in ZONES][:6]

    async def one(z):
        try:
            return {"zone": z, "price": ZONES[z], "taken": await asyncio.wait_for(is_taken(name, z), 9)}
        except Exception as e:
            log.info("Tekshirib bo'lmadi: %s%s (%r)", name, z, e)
            return {"zone": z, "price": ZONES[z], "taken": False, "unknown": True}

    return web.json_response({"results": await asyncio.gather(*(one(z) for z in zones))})


@routes.post("/api/sms/send")
async def api_sms_send(request):
    user = await auth(request)
    phone = norm_phone((await body_of(request)).get("phone"))
    if not phone:
        return web.json_response({"ok": False, "error": "invalid_phone"})
    old = codes.get(phone)
    # Agar aktiv kod bo'lsa va 15 soniyadan kam vaqt o'tgan bo'lsa o'sha kodni qaytaramiz
    if old and old["exp"] > time.time() and time.time() - old["sent"] < 15:
        return web.json_response({
            "ok": True,
            "via": "screen",
            "code": old["code"],
            "phone": "+" + phone,
            "ttl": int(old["exp"] - time.time())
        })
    code = str(secrets.randbelow(90000) + 10000)
    codes[phone] = {"code": code, "exp": time.time() + 300, "tries": 0, "sent": time.time()}
    if CODE_VIA == "bot" and bot and user.get("id"):
        try:
            await bot.send_message(
                user["id"],
                f"🔐 Tasdiqlash kodingiz: <code>{code}</code>\n\nKod 5 daqiqa amal qiladi.",
                parse_mode="HTML")
        except Exception as e:
            log.warning("Kodni botdan yuborib bo'lmadi: %r", e)
    elif ESKIZ_EMAIL:
        try:
            await send_sms(phone, f"Domen.uz tasdiqlash kodi: {code}")
        except Exception as e:
            log.warning("Eskiz SMS xato: %r", e)
    # Saytda ekranda doimiy ko'rinib turishi uchun kodni javobda ham qaytaramiz
    return web.json_response({
        "ok": True,
        "via": "screen",
        "code": code,
        "phone": "+" + phone,
        "ttl": 300
    })


@routes.post("/api/sms/verify")
async def api_sms_verify(request):
    await auth(request)
    b = await body_of(request)
    phone = norm_phone(b.get("phone"))
    c = codes.get(phone or "")
    if not c or c["exp"] < time.time():
        return web.json_response({"ok": False})
    c["tries"] += 1
    if c["tries"] > 5 or str(b.get("code", "")).strip() != c["code"]:
        return web.json_response({"ok": False})
    codes.pop(phone, None)
    verified[phone] = time.time() + 600
    return web.json_response({"ok": True})


@routes.post("/api/order")
async def api_order(request):
    user = await auth(request)
    b = await body_of(request)
    name, zone, phone = str(b.get("name", "")).lower(), b.get("zone"), norm_phone(b.get("phone"))
    try:
        years = min(3, max(1, int(b.get("years", 1))))
    except (TypeError, ValueError):
        years = 1
    if not phone or not NAME_RE.match(name) or zone not in ZONES or verified.get(phone, 0) < time.time():
        return web.json_response({"ok": False})
    verified.pop(phone, None)
    oid = "D-" + secrets.token_hex(2).upper()
    total = ZONES[zone] * years
    db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?)",
               (oid, name + zone, years, total, phone, user.get("id", 0), user.get("username", ""),
                user.get("first_name", "Web"), time.strftime("%Y-%m-%d %H:%M:%S")))
    db.commit()
    who = f"@{user['username']}" if user.get("username") else user.get("first_name", "Web/App foydalanuvchisi")
    msg = (f"🆕 Yangi buyurtma {oid}\nDomen: {name}{zone}\nMuddat: {years} yil\n"
           f"Summa: {total:,} so'm\nTelefon: +{phone}\nFoydalanuvchi: {who} (id {user.get('id', 0)})").replace(",", " ")
    admin = kv_get("admin")
    if admin and bot:
        asyncio.create_task(bot.send_message(int(admin), msg))
    else:
        log.info("Yangi buyurtma:\n%s", msg)
    return web.json_response({"ok": True, "id": oid})


@web.middleware
async def middleware(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            resp = e
        except Exception:
            log.exception("API xato")
            resp = web.json_response({"ok": False}, status=500)
    resp.headers.update({"Access-Control-Allow-Origin": "*",
                         "Access-Control-Allow-Headers": "Content-Type, X-Init-Data",
                         "Access-Control-Allow-Methods": "GET, POST, OPTIONS"})
    if request.path.startswith("/api/"):
        log.info("%s %s %s", request.method, request.path, resp.status)
    return resp


# ---------- Bot ----------
dp = Dispatcher()


@dp.message(CommandStart())
async def on_start(m: Message):
    u = m.from_user
    is_admin = (u.username or "").lower() == ADMIN_USERNAME.lower()
    if is_admin:
        kv_set("admin", m.chat.id)
    text = (f"Salom, {u.first_name}! 👋\n\n"
            f"Domen.uz: domen band yoki bo'shligini tekshiring, buyurtma bering yoki ilovamizni yuklab oling.")
    kb = None
    if WEBAPP_URL:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🌐 Domen qidirish (Mini App)", web_app=WebAppInfo(url=WEBAPP_URL))],
            [InlineKeyboardButton(text="📲 Ilovani yuklab olish (Android / iOS)", url=f"{WEBAPP_URL}/download")]
        ])
    else:
        text += "\n\n⏳ Mini app tayyorlanmoqda, 1 daqiqadan keyin /start ni qayta yozing."
    await m.answer(text, reply_markup=kb)
    if is_admin:
        await m.answer("✅ Siz admin sifatida ulandingiz. Yangi buyurtmalar shu yerga keladi.")


@dp.message(Command("id"))
async def on_id(m: Message):
    await m.answer(f"Sizning ID: {m.from_user.id}")


async def start_tunnel():
    exe = BASE / "cloudflared"
    if not exe.exists():
        arch = "arm64" if platform.machine() in ("aarch64", "arm64") else "amd64"
        url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
        log.info("cloudflared yuklanmoqda...")
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=180)) as r:
            r.raise_for_status()
            exe.write_bytes(await r.read())
        exe.chmod(0o755)
    proc = await asyncio.create_subprocess_exec(
        str(exe), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{LOCAL_PORT}",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    fut = asyncio.get_running_loop().create_future()

    async def pump():
        async for raw in proc.stderr:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", raw.decode(errors="ignore"))
            if m and not fut.done():
                fut.set_result(m.group(0))

    tasks.append(asyncio.create_task(pump()))
    return await asyncio.wait_for(fut, 60), proc


async def tunnel_keeper():
    global WEBAPP_URL
    while True:
        wait = 5
        try:
            WEBAPP_URL, proc = await start_tunnel()
            log.info("Mini app manzili: %s", WEBAPP_URL)
            log.info("Ilovani yuklab olish sahifasi: %s/download", WEBAPP_URL)
            if bot:
                await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
                    text="Domen.uz", web_app=WebAppInfo(url=WEBAPP_URL)))
            await proc.wait()
            log.warning("cloudflared to'xtadi, qayta ishga tushirilmoqda...")
        except Exception as e:
            log.error("Tunnel xato: %r", e)
            wait = 30
        await asyncio.sleep(wait)


async def main():
    global bot, session, bot_username, LOCAL_PORT
    session = aiohttp.ClientSession()
    if BOT_TOKEN:
        bot = Bot(BOT_TOKEN)
        try:
            bot_username = (await bot.get_me()).username
            log.info("Bot ulandi: @%s", bot_username)
        except Exception as e:
            log.warning("Bot token ulanmadi, faqat Web/App server rejimida ishlaydi (%s)", e)
            await bot.session.close()
            bot = None
    app = web.Application(middlewares=[middleware])
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, HOST, PORT).start()
    except OSError:
        await web.TCPSite(runner, HOST, 0).start()
    LOCAL_PORT = runner.addresses[0][1]
    log.info("Server ishlayapti: http://%s:%s (Yuklab olish: http://%s:%s/download)",
             HOST, LOCAL_PORT, HOST, LOCAL_PORT)
    if bot:
        await bot.delete_webhook()
        await bot.set_my_commands([BotCommand(command="start", description="Boshlash")])
        if WEBAPP_URL:
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Domen.uz", web_app=WebAppInfo(url=WEBAPP_URL)))
        else:
            tasks.append(asyncio.create_task(tunnel_keeper()))
        try:
            await dp.start_polling(bot, allowed_updates=["message"])
        finally:
            await session.close()
            await runner.cleanup()
            await bot.session.close()
    else:
        try:
            await asyncio.Event().wait()
        finally:
            await session.close()
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
