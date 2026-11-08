"""TG Logger: Telethon userbot + FastAPI web UI."""
import asyncio, html, os, sqlite3, sys
from datetime import timezone, datetime, timedelta
from pathlib import Path

# Гарантируем UTF-8 в консоли Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Автозагрузка настроек из файла .env (если он существует)
_env_path = Path(".env")
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from telethon import TelegramClient, events, utils

# Конфигурация API (получите на https://my.telegram.org)
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")

if not API_ID or not API_HASH:
    print("=" * 60)
    print("Telegram API_ID или API_HASH не найдены.")
    print("Получите их бесплатно на https://my.telegram.org")
    print("Вы можете указать их в файле .env (см. .env.example) или ввести сейчас:")
    print("=" * 60)
    while not API_ID:
        try:
            val = input("Введите ваш API_ID (число): ").strip()
            if val:
                API_ID = int(val)
        except ValueError:
            print("API_ID должен быть целым числом.")
    while not API_HASH:
        API_HASH = input("Введите ваш API_HASH: ").strip()

HOST, PORT = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", 8000))
AV = Path("avatars")
AV.mkdir(exist_ok=True)
MEDIA = Path("media")
MEDIA.mkdir(exist_ok=True)

MONTHS_NOM = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель", 5: "Май", 6: "Июнь",
    7: "Июль", 8: "Август", 9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

MONTHS_GEN = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}

# ---------- БД ----------
db = sqlite3.connect("logs.db", check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT, 
    msg_id INTEGER, 
    chat_id INTEGER,
    chat_title TEXT, 
    chat_username TEXT, 
    text TEXT, 
    date TEXT, 
    avatar TEXT,
    reply_text TEXT,
    media_file TEXT,
    is_deleted INTEGER DEFAULT 0,
    orig_text TEXT,
    is_edited INTEGER DEFAULT 0,
    reply_media_file TEXT
)""")
db.commit()

# Добавляем новые колонки при необходимости
for col in [
    "reply_text TEXT", 
    "media_file TEXT", 
    "is_deleted INTEGER DEFAULT 0", 
    "orig_text TEXT", 
    "is_edited INTEGER DEFAULT 0",
    "reply_media_file TEXT"
]:
    try:
        db.execute(f"ALTER TABLE logs ADD COLUMN {col}")
        db.commit()
    except Exception:
        pass

# ---------- Telethon ----------
client = TelegramClient("session", API_ID, API_HASH)
chats = {}  # chat_id -> (title, username, avatar_file)
my_id = None


async def download_msg_media(msg, prefix=""):
    """Скачивает фото, видео, гифки или голосовые сообщения"""
    if not msg or not msg.media:
        return None
    try:
        cid = getattr(msg, "chat_id", None) or getattr(msg.peer_id, "user_id", "0")
        
        # 1. Голосовое сообщение или аудио
        if getattr(msg, "voice", None) or getattr(msg, "audio", None):
            fname = f"{prefix}voice_{cid}_{msg.id}.ogg"
            target = str(MEDIA / fname)
            if not os.path.exists(target):
                res = await client.download_media(msg, file=target)
                if res and os.path.exists(target):
                    return fname
            else:
                return fname

        # 2. Видео / GIF / кружочек
        doc = getattr(msg, "document", None)
        mime = getattr(doc, "mime_type", "") if doc else ""
        if getattr(msg, "video", None) or getattr(msg, "gif", None) or mime.startswith("video/"):
            fname_mp4 = f"{prefix}{cid}_{msg.id}.mp4"
            target_mp4 = str(MEDIA / fname_mp4)
            size = getattr(doc, "size", 0) if doc else 0
            if size < 30 * 1024 * 1024:
                if not os.path.exists(target_mp4):
                    res = await client.download_media(msg, file=target_mp4)
                    if res and os.path.exists(target_mp4):
                        return fname_mp4
                else:
                    return fname_mp4
            else:
                fname_jpg = f"{prefix}{cid}_{msg.id}_thumb.jpg"
                target_jpg = str(MEDIA / fname_jpg)
                if not os.path.exists(target_jpg):
                    res = await client.download_media(msg, file=target_jpg, thumb=-1)
                    if res and os.path.exists(target_jpg):
                        return fname_jpg
                else:
                    return fname_jpg

        # 3. Фото
        if getattr(msg, "photo", None) or mime.startswith("image/"):
            fname = f"{prefix}{cid}_{msg.id}.jpg"
            target = str(MEDIA / fname)
            if not os.path.exists(target):
                res = await client.download_media(msg, file=target)
                if res and os.path.exists(target):
                    return fname
            else:
                return fname

    except Exception:
        pass
    return None


async def chat_info(event):
    cid = event.chat_id
    if cid not in chats:
        chat = await event.get_chat()
        title = utils.get_display_name(chat) or str(cid)
        username = getattr(chat, "username", None)
        avatar = None
        try:
            if await client.download_profile_photo(chat, file=str(AV / f"{cid}.jpg"), download_big=False):
                avatar = f"{cid}.jpg"
        except Exception:
            pass
        chats[cid] = (title, username, avatar)
    return chats[cid]


@client.on(events.NewMessage())
async def on_message(event):
    global my_id
    m = event.message

    if m.out or (my_id and m.sender_id == my_id):
        return

    reply_text = None
    reply_media_file = None

    if event.is_private:
        if m.is_reply:
            try:
                rep = await m.get_reply_message()
                if rep:
                    reply_text = rep.message or ""
                    reply_media_file = await download_msg_media(rep, prefix="rep_")
            except Exception:
                pass
    else:
        if not m.is_reply:
            return
        try:
            rep = await m.get_reply_message()
            if not rep:
                return
            if rep.sender_id != my_id and not rep.out:
                return
            reply_text = rep.message or ""
            reply_media_file = await download_msg_media(rep, prefix="rep_")
        except Exception:
            return

    media_file = await download_msg_media(m, prefix="")
    text = m.message or ""
    if not text and not media_file:
        return

    title, username, avatar = await chat_info(event)
    db.execute(
        "INSERT INTO logs(msg_id,chat_id,chat_title,chat_username,text,date,avatar,reply_text,media_file,is_deleted,orig_text,is_edited,reply_media_file) "
        "VALUES(?,?,?,?,?,?,?,?,?,0,NULL,0,?)",
        (
            m.id,
            event.chat_id,
            title,
            username,
            text,
            m.date.astimezone(timezone.utc).isoformat(),
            avatar,
            reply_text,
            media_file,
            reply_media_file,
        ),
    )
    db.commit()


@client.on(events.MessageEdited())
async def on_edited(event):
    global my_id
    m = event.message
    if m.out or (my_id and m.sender_id == my_id):
        return

    new_text = m.message or ""
    if not new_text and not m.media:
        return

    row = db.execute(
        "SELECT id, text, orig_text FROM logs WHERE chat_id = ? AND msg_id = ?",
        (event.chat_id, m.id),
    ).fetchone()

    if row:
        log_id, current_text, orig_text = row
        # Игнорируем технические события, если текст не изменился
        if (new_text or "") == (current_text or ""):
            return
        if not orig_text:
            orig_text = current_text
        db.execute(
            "UPDATE logs SET text = ?, orig_text = ?, is_edited = 1 WHERE id = ?",
            (new_text, orig_text, log_id),
        )
        db.commit()
    else:
        reply_text = None
        reply_media_file = None
        if not event.is_private:
            if not m.is_reply:
                return
            try:
                rep = await m.get_reply_message()
                if not rep or (rep.sender_id != my_id and not rep.out):
                    return
                reply_text = rep.message or ""
                reply_media_file = await download_msg_media(rep, prefix="rep_")
            except Exception:
                return

        media_file = await download_msg_media(m, prefix="")
        title, username, avatar = await chat_info(event)
        db.execute(
            "INSERT INTO logs(msg_id,chat_id,chat_title,chat_username,text,date,avatar,reply_text,media_file,is_deleted,orig_text,is_edited,reply_media_file) "
            "VALUES(?,?,?,?,?,?,?,?,?,0,?,1,?)",
            (
                m.id,
                event.chat_id,
                title,
                username,
                new_text,
                m.date.astimezone(timezone.utc).isoformat(),
                avatar,
                reply_text,
                media_file,
                "[текст до запуска]",
                reply_media_file,
            ),
        )
        db.commit()


@client.on(events.MessageDeleted())
async def on_deleted(event):
    if not event.deleted_ids:
        return
    try:
        placeholders = ",".join(["?"] * len(event.deleted_ids))
        if event.chat_id:
            sql = f"UPDATE logs SET is_deleted = 1 WHERE chat_id = ? AND msg_id IN ({placeholders})"
            db.execute(sql, [event.chat_id] + list(event.deleted_ids))
        else:
            sql = f"UPDATE logs SET is_deleted = 1 WHERE msg_id IN ({placeholders})"
            db.execute(sql, list(event.deleted_ids))
        db.commit()
    except Exception:
        pass


# ---------- Web ----------
app = FastAPI()
app.mount("/avatars", StaticFiles(directory=AV), name="avatars")
app.mount("/media", StaticFiles(directory=MEDIA), name="media")

PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>TG Logger</title>
<!-- Шрифт Onest -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Onest:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
html{scroll-behavior:smooth}
body{margin:0;background:#0a0a0a;color:#fff;font-family:'Onest',system-ui,-apple-system,sans-serif;font-weight:500;font-size:15px;line-height:1.45;letter-spacing:-0.01em}
.w{max-width:1200px;margin:0 auto;padding:16px}

/* Панель фильтров */
.toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:16px}
.search-box{flex:1;min-width:220px}
.search-box input{width:100%;box-sizing:border-box;margin-bottom:0;padding:10px 14px;background:#141414;color:#fff;
 border:1px solid #222;border-radius:6px;font:inherit;outline:none;transition:border-color 0.15s;height:42px}
.search-box input:focus{border-color:#444}

.toolbar-divider{width:1px;height:24px;background:#222;margin:0 4px}
.pills{display:flex;align-items:center;flex-wrap:wrap;gap:6px}
.pill{background:#141414;color:#888;border:1px solid #222;border-radius:6px;padding:8px 14px;font:500 13px 'Onest',sans-serif;cursor:pointer;transition:all 0.15s;user-select:none;height:42px;box-sizing:border-box}
.pill:hover{background:#1a1a1a;color:#ccc;border-color:#333}
.pill.active{background:#262626;color:#fff;border-color:#4a4a4a}

.filter-tag{
  display:inline-flex;
  align-items:center;
  gap:6px;
  border-radius:6px;
  font-size:13px;
  font-weight:500;
  height:42px;
  box-sizing:border-box;
  white-space:nowrap;
  overflow:hidden;
  max-width:0;
  opacity:0;
  padding:0;
  margin-left:-8px;
  border:0 solid transparent;
  transform:translateX(16px);
  pointer-events:none;
  transition:max-width 0.35s cubic-bezier(0.16, 1, 0.3, 1),
             opacity 0.25s cubic-bezier(0.16, 1, 0.3, 1),
             transform 0.35s cubic-bezier(0.16, 1, 0.3, 1),
             padding 0.35s cubic-bezier(0.16, 1, 0.3, 1),
             margin-left 0.35s cubic-bezier(0.16, 1, 0.3, 1),
             border-color 0.2s;
}
.filter-tag.visible{
  max-width:320px;
  opacity:1;
  padding:6px 12px;
  margin-left:0;
  border-width:1px;
  transform:translateX(0);
  pointer-events:auto;
}
.chat-tag{background:#142218;color:#4ade80;border-style:solid;border-color:#22542e}
.date-tag{background:#1a1926;color:#c4b5fd;border-style:solid;border-color:#3b3563}
.tag-close{cursor:pointer;opacity:0.75;font-weight:700;margin-left:4px;padding:0 2px}
.tag-close:hover{opacity:1;color:#fff}

.reset-btn{
  background:none;
  border:none;
  color:#666;
  font:inherit;
  font-size:13px;
  cursor:pointer;
  text-decoration:underline;
  height:42px;
  box-sizing:border-box;
  display:inline-flex;
  align-items:center;
  white-space:nowrap;
  overflow:hidden;
  max-width:0;
  opacity:0;
  padding:0;
  margin-left:-8px;
  transform:translateX(16px);
  pointer-events:none;
  transition:max-width 0.35s cubic-bezier(0.16, 1, 0.3, 1),
             opacity 0.25s cubic-bezier(0.16, 1, 0.3, 1),
             transform 0.35s cubic-bezier(0.16, 1, 0.3, 1),
             padding 0.35s cubic-bezier(0.16, 1, 0.3, 1),
             margin-left 0.35s cubic-bezier(0.16, 1, 0.3, 1),
             color 0.15s;
}
.reset-btn:hover{color:#aaa}
.reset-btn.visible{
  max-width:130px;
  opacity:1;
  padding:6px 10px;
  margin-left:0;
  transform:translateX(0);
  pointer-events:auto;
}

table{width:100%;border-collapse:collapse}

/* Разделители по датам со сворачиванием */
tr.sep-row td{padding:0;background:#0a0a0a !important;border:none}
tr.sep-row:hover td{background:#0a0a0a !important}

.chevron-svg{
  transition:transform 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  display:inline-block;
  vertical-align:middle;
  flex-shrink:0;
  opacity:0.6;
}
.sep-year{
  font-size:22px;
  font-weight:700;
  color:#fff;
  letter-spacing:-0.02em;
  padding:32px 4px 10px;
  border-bottom:1px solid #222;
  cursor:pointer;
  display:flex;
  align-items:center;
  gap:10px;
  user-select:none;
  transition:color 0.15s;
}
.sep-year:hover{color:#e5e5e5}
.sep-year:hover .chevron-svg{opacity:1;color:#fff}
.sep-year.collapsed .chevron-svg{transform:rotate(-90deg)}

.sep-month{
  font-size:16px;
  font-weight:600;
  color:#a3a3a3;
  padding:22px 6px 8px;
  cursor:pointer;
  display:inline-flex;
  align-items:center;
  gap:8px;
  user-select:none;
  transition:color 0.15s;
}
.sep-month:hover{color:#fff}
.sep-month:hover .chevron-svg{opacity:1;color:#fff}
.sep-month.collapsed .chevron-svg{transform:rotate(-90deg)}

.sep-day{
  display:flex;
  align-items:center;
  gap:12px;
  padding:12px 6px 8px;
}
.sep-day-badge{
  display:inline-flex;
  align-items:center;
  background:#161616;
  border:1px solid #262626;
  border-radius:12px;
  padding:2px 4px 2px 10px;
  gap:4px;
  transition:all 0.15s;
  user-select:none;
}
.sep-day-badge:hover{
  border-color:#383838;
  background:#1c1c1c;
}
.sep-day-text{
  color:#bbb;
  font-size:12px;
  font-weight:500;
  white-space:nowrap;
  cursor:pointer;
  transition:color 0.15s;
}
.sep-day-text:hover{
  color:#fff;
}
.sep-day-toggle{
  background:none;
  border:none;
  color:#777;
  cursor:pointer;
  padding:2px 4px;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  border-radius:6px;
  transition:all 0.15s;
}
.sep-day-toggle:hover{
  color:#fff;
  background:#2a2a2a;
}
.sep-day-badge.collapsed .chevron-svg{transform:rotate(-90deg)}

.sep-day-line{
  flex:1;
  height:1px;
  background:#1e1e1e;
}

/* Строки сообщений (зебра) - серый прямоугольник на ховер полностью убран */
tr.row-odd{background:#0d0d0d}
tr.row-even{background:#141414}
tr:hover{background:none}
tr.row-odd:hover{background:#0d0d0d}
tr.row-even:hover{background:#141414}

/* Пустое состояние */
tr.empty-row td{background:transparent !important;padding:48px 10px;text-align:center;color:#666;font-size:14px;border:none}
tr.empty-row:hover td{background:transparent !important}

td{padding:10px 14px;vertical-align:top}
.n{width:50px;color:#666;font-size:13px;font-weight:400;line-height:1.45;padding-top:12px}
.t{white-space:pre-wrap;word-break:break-word}
.d{width:80px;color:#8a8a8a;font-weight:400;white-space:nowrap;font-size:13px;line-height:1.45;padding-top:12px;font-variant-numeric:tabular-nums}
.s{width:330px}.s div{display:flex;align-items:center;gap:8px}
.s a.ava-link{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;flex:none;text-decoration:none}
.s img{width:22px;height:22px;flex:none;border-radius:50%;object-fit:cover;background:#333;display:block}
.s i{width:22px;height:22px;flex:none;border-radius:50%;background:#262626;border:1px solid #383838;
 display:inline-flex;align-items:center;justify-content:center;font:600 11px/1 'Onest',sans-serif;font-style:normal;
 text-align:center;color:#ccc;box-sizing:border-box;user-select:none}
.s a{color:#bbb;text-decoration:none;font-weight:500;cursor:pointer;transition:color 0.15s}
.s a:hover{color:#fff;text-decoration:underline}
.s span{color:#777;font-weight:400}

/* Стили для реплаев (акцентный серый) */
.reply-wrap{display:inline-flex;align-items:center;flex-wrap:wrap;gap:6px}
.reply-orig{background:#181818;color:#8a8a8a;padding:2px 8px;border-radius:4px;font-size:13px;
 border-left:3px solid #5a5a5a;max-width:280px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.reply-arrow{display:inline-block;vertical-align:middle;margin:0 6px;flex-shrink:0;opacity:0.75;transition:opacity 0.15s,transform 0.15s}
.reply-wrap:hover .reply-arrow{opacity:1;transform:translateX(1px)}
.reply-text{
  display:inline-block;
  vertical-align:baseline;
  color:#fff;
  transition:opacity 0.16s cubic-bezier(0.16, 1, 0.3, 1),
             transform 0.16s cubic-bezier(0.16, 1, 0.3, 1),
             filter 0.16s cubic-bezier(0.16, 1, 0.3, 1),
             color 0.2s;
}
.reply-text.diff-fade-out{
  opacity:0;
  transform:scale(0.97) translateY(-2px);
  filter:blur(2px);
}
.reply-text.diff-fade-in{
  animation:diffFadeIn 0.22s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}
@keyframes diffFadeIn{
  from{opacity:0;transform:scale(0.97) translateY(2px);filter:blur(2px)}
  to{opacity:1;transform:scale(1) translateY(0);filter:blur(0)}
}
.reply-text.is-orig{color:#a8a29e;text-decoration:line-through;opacity:0.85}

/* Apple Emoji стиль */
.apple-emoji{width:1.2em;height:1.2em;vertical-align:-0.2em;margin:0 0.12em;display:inline-block}

/* Скругленный квадрат для фото / видео */
.media-thumb{width:32px;height:32px;border-radius:6px;border:1px solid #383838;background:#181818;
 object-fit:cover;display:inline-block;vertical-align:middle;cursor:pointer;transition:transform 0.15s, border-color 0.15s;flex-shrink:0}
.media-thumb:hover{border-color:#666;transform:scale(1.05)}

/* Голосовое сообщение */
.voice-box{display:inline-flex;align-items:center;gap:7px;background:#181818;border:1px solid #383838;
 border-radius:14px;padding:3px 9px 3px 5px;vertical-align:middle;height:26px;box-sizing:border-box;user-select:none}
.voice-btn{width:20px;height:20px;border-radius:50%;background:#2e2e2e;border:1px solid #444;color:#ddd;
 display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:10px;padding:0;transition:background 0.2s}
.voice-btn:hover{background:#444;color:#fff}
.voice-bar{width:75px;height:4px;background:#333;border-radius:2px;cursor:pointer;position:relative}
.voice-prog{width:0%;height:100%;background:#888;border-radius:2px;pointer-events:none}
.voice-time{color:#888;font-size:11px;font-variant-numeric:tabular-nums;min-width:26px}

/* Бейджи и кнопки редактирования */
.edit-badge{display:inline-flex;align-items:center;background:#1e1e1e;color:#858585;border:1px solid #333;
 font-size:11px;font-weight:500;padding:1px 5px;border-radius:4px;margin-left:6px;vertical-align:middle}
.diff-btn{background:#1c1c1c;color:#999;border:1px solid #383838;font:500 11px/1.2 'Onest',sans-serif;
 padding:2px 7px;border-radius:4px;cursor:pointer;margin-left:6px;vertical-align:middle;transition:all 0.2s}
.diff-btn:hover{background:#2a2a2a;color:#ddd;border-color:#555}
.diff-btn.active{background:#2c281a;color:#eab308;border-color:#5e4e22}

/* Бейдж удаленного сообщения */
.del-badge{display:inline-flex;align-items:center;background:#2b1414;color:#f87171;border:1px solid #582121;
 font-size:11px;font-weight:500;padding:1px 6px;border-radius:4px;margin-left:6px;vertical-align:middle}

/* Модалка просмотра фото и видео */
.modal{display:none;position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.85);
 z-index:9999;justify-content:center;align-items:center}

@media(max-width:700px){.s{width:auto}.d{width:auto}}
</style></head><body><div class="w">

<div class="toolbar">
  <div class="search-box">
    <input id="searchInput" placeholder="Поиск по тексту или чату…" oninput="onSearchInput(this.value)">
  </div>

  <div class="pills">
    <button class="pill active" data-filter="all" onclick="setFilter('all')">Все</button>
    <button class="pill" data-filter="deleted" onclick="setFilter('deleted')">Удалённые</button>
    <button class="pill" data-filter="edited" onclick="setFilter('edited')">Изменённые</button>
    <button class="pill" data-filter="media" onclick="setFilter('media')">С медиа</button>
    <div class="toolbar-divider"></div>
    <button id="pillToday" class="pill" onclick="toggleQuickDate('today')">Сегодня</button>
    <button id="pillYesterday" class="pill" onclick="toggleQuickDate('yesterday')">Вчера</button>
  </div>

  <div id="dateFilterTag" class="filter-tag date-tag">
    <span>📅</span> <span id="dateFilterName"></span>
    <span class="tag-close" onclick="clearDateFilter()" title="Сбросить фильтр даты">✕</span>
  </div>

  <div id="chatFilterTag" class="filter-tag chat-tag">
    <span>Чат:</span> <span id="chatFilterName"></span>
    <span class="tag-close" onclick="clearChatFilter()" title="Сбросить фильтр чата">✕</span>
  </div>

  <button id="resetAllBtn" class="reset-btn" onclick="resetAllFilters()">Сбросить всё</button>
</div>

<table><tbody id="tb">%ROWS%</tbody></table></div>

<div id="modal" class="modal" onclick="closeModal()"></div>

<script>
const urlParams = new URLSearchParams(location.search);
let currentQ = urlParams.get('q') || '';
let currentFilter = urlParams.get('filter') || 'all';
let currentChat = urlParams.get('chat') || '';
let currentChatTitle = urlParams.get('chat_title') || '';
let currentDate = urlParams.get('date') || '';
let currentDateLabel = urlParams.get('date_label') || '';

const showingOrig = new Set();
let currentAudio = null;
let currentAudioSrc = null;

// Инициализация полей
document.getElementById('searchInput').value = currentQ;
updateDateUI();
updatePillsUI();
updateChatTagUI();

const monthsGen = ['', 'января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

function formatDateLabel(val) {
  if (!val) return '';
  if (val === 'today') return 'Сегодня';
  if (val === 'yesterday') return 'Вчера';
  const parts = val.split('-');
  if (parts.length === 3) {
    const d = parseInt(parts[2], 10);
    const m = parseInt(parts[1], 10);
    const mStr = monthsGen[m] || parts[1];
    return d + ' ' + mStr;
  }
  return val;
}

function updateDateUI() {
  const pillToday = document.getElementById('pillToday');
  const pillYesterday = document.getElementById('pillYesterday');
  const tag = document.getElementById('dateFilterTag');
  const name = document.getElementById('dateFilterName');

  if (pillToday) pillToday.classList.toggle('active', currentDate === 'today');
  if (pillYesterday) pillYesterday.classList.toggle('active', currentDate === 'yesterday');

  if (currentDate && currentDate !== 'today' && currentDate !== 'yesterday') {
    name.textContent = currentDateLabel || formatDateLabel(currentDate);
    tag.classList.add('visible');
  } else {
    tag.classList.remove('visible');
  }
}

function toggleQuickDate(mode) {
  if (currentDate === mode) {
    currentDate = '';
    currentDateLabel = '';
  } else {
    currentDate = mode;
    currentDateLabel = (mode === 'today') ? 'Сегодня' : 'Вчера';
  }
  updateDateUI();
  updateURL();
  fetchRows(true);
}

function filterDate(dateStr, label) {
  currentDate = dateStr;
  currentDateLabel = label || formatDateLabel(dateStr);
  updateDateUI();
  updateURL();
  fetchRows(true);
}

function clearDateFilter() {
  currentDate = '';
  currentDateLabel = '';
  updateDateUI();
  updateURL();
  fetchRows(true);
}

function updatePillsUI() {
  document.querySelectorAll('.pill[data-filter]').forEach(p => {
    p.classList.toggle('active', p.getAttribute('data-filter') === currentFilter);
  });
}

function updateChatTagUI() {
  const tag = document.getElementById('chatFilterTag');
  const name = document.getElementById('chatFilterName');
  const resetBtn = document.getElementById('resetAllBtn');
  if (currentChat) {
    name.textContent = currentChatTitle || ('ID: ' + currentChat);
    tag.classList.add('visible');
  } else {
    tag.classList.remove('visible');
  }
  const hasFilter = currentQ || currentFilter !== 'all' || currentChat || currentDate;
  if (hasFilter) {
    resetBtn.classList.add('visible');
  } else {
    resetBtn.classList.remove('visible');
  }
}

function updateURL() {
  const p = new URLSearchParams();
  if (currentQ) p.set('q', currentQ);
  if (currentFilter && currentFilter !== 'all') p.set('filter', currentFilter);
  if (currentChat) {
    p.set('chat', currentChat);
    if (currentChatTitle) p.set('chat_title', currentChatTitle);
  }
  if (currentDate) {
    p.set('date', currentDate);
    if (currentDateLabel) p.set('date_label', currentDateLabel);
  }
  const newRel = location.pathname + (p.toString() ? '?' + p.toString() : '');
  history.replaceState(null, '', newRel);
  updateChatTagUI();
}

let searchTimer = null;
function onSearchInput(val) {
  currentQ = val.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    updateURL();
    fetchRows(true);
  }, 200);
}

function setFilter(f) {
  currentFilter = f;
  if (f === 'all') {
    // Мгновенный сброс даты и чата при переходе на "Все", чтобы сразу отобразились все сообщения
    currentDate = '';
    currentDateLabel = '';
    currentChat = '';
    currentChatTitle = '';
    updateDateUI();
  }
  updatePillsUI();
  updateURL();
  fetchRows(true);
}

function filterChat(cid, title) {
  currentChat = String(cid);
  currentChatTitle = title || '';
  updateURL();
  fetchRows(true);
}

function clearChatFilter() {
  currentChat = '';
  currentChatTitle = '';
  updateURL();
  fetchRows(true);
}

function resetAllFilters() {
  currentQ = '';
  currentFilter = 'all';
  currentChat = '';
  currentChatTitle = '';
  currentDate = '';
  currentDateLabel = '';
  collapsedYears.clear();
  collapsedMonths.clear();
  collapsedDays.clear();
  document.getElementById('searchInput').value = '';
  updateDateUI();
  updatePillsUI();
  updateURL();
  fetchRows(true);
}

// Управление сворачиванием дат (Год / Месяц / День)
const collapsedYears = new Set();
const collapsedMonths = new Set();
const collapsedDays = new Set();

function toggleCollapseYear(year, el, e) {
  if (e) e.stopPropagation();
  const yStr = String(year);
  if (collapsedYears.has(yStr)) {
    collapsedYears.delete(yStr);
  } else {
    collapsedYears.add(yStr);
  }
  applyCollapsedState();
}

function toggleCollapseMonth(monthStr, el, e) {
  if (e) e.stopPropagation();
  if (collapsedMonths.has(monthStr)) {
    collapsedMonths.delete(monthStr);
  } else {
    collapsedMonths.add(monthStr);
  }
  applyCollapsedState();
}

function toggleCollapseDay(dayStr, btn, e) {
  if (e) e.stopPropagation();
  if (collapsedDays.has(dayStr)) {
    collapsedDays.delete(dayStr);
  } else {
    collapsedDays.add(dayStr);
  }
  applyCollapsedState();
}

function applyCollapsedState() {
  document.querySelectorAll('.sep-year-row').forEach(row => {
    const yr = row.getAttribute('data-sep-year');
    const isCol = collapsedYears.has(yr);
    const box = row.querySelector('.sep-year');
    if (box) box.classList.toggle('collapsed', isCol);
  });

  document.querySelectorAll('.sep-month-row').forEach(row => {
    const yr = row.getAttribute('data-sep-year');
    const mo = row.getAttribute('data-sep-month');
    const isHiddenByYear = collapsedYears.has(yr);
    row.style.display = isHiddenByYear ? 'none' : '';
    const isCol = collapsedMonths.has(mo);
    const box = row.querySelector('.sep-month');
    if (box) box.classList.toggle('collapsed', isCol);
  });

  document.querySelectorAll('.sep-day-row').forEach(row => {
    const yr = row.getAttribute('data-sep-year');
    const mo = row.getAttribute('data-sep-month');
    const dy = row.getAttribute('data-sep-day');
    const isHidden = collapsedYears.has(yr) || collapsedMonths.has(mo);
    row.style.display = isHidden ? 'none' : '';
    const isCol = collapsedDays.has(dy);
    const badge = row.querySelector('.sep-day-badge');
    if (badge) badge.classList.toggle('collapsed', isCol);
  });

  document.querySelectorAll('.msg-row').forEach(row => {
    const yr = row.getAttribute('data-msg-year');
    const mo = row.getAttribute('data-msg-month');
    const dy = row.getAttribute('data-msg-day');
    const isHidden = collapsedYears.has(yr) || collapsedMonths.has(mo) || collapsedDays.has(dy);
    row.style.display = isHidden ? 'none' : '';
  });
}

// Замена всех эмодзи на Apple Emoji
function applyAppleEmojis(root) {
  if (!root) return;
  const regex = /(\p{Extended_Pictographic}(?:\u200d\p{Extended_Pictographic})*)/gu;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
  const nodes = [];
  while(walker.nextNode()) {
    if (regex.test(walker.currentNode.nodeValue)) {
      nodes.push(walker.currentNode);
    }
  }
  nodes.forEach(node => {
    const parent = node.parentNode;
    if(parent && (parent.tagName === 'SCRIPT' || parent.tagName === 'STYLE' || parent.classList.contains('apple-emoji'))) return;
    const span = document.createElement('span');
    span.innerHTML = node.nodeValue.replace(regex, m => 
      `<img class="apple-emoji" src="https://emojicdn.elk.sh/${encodeURIComponent(m)}?style=apple" alt="${m}" draggable="false" loading="lazy">`
    );
    parent.replaceChild(span, node);
  });
}

// Запускаем при загрузке
applyAppleEmojis(document.getElementById('tb'));
applyCollapsedState();

let isFetching = false;
let currentAbortController = null;
async function fetchRows(force = false) {
  if (isFetching && !force) return;
  if (force && currentAbortController) {
    currentAbortController.abort();
  }
  currentAbortController = new AbortController();
  isFetching = true;
  try {
    const p = new URLSearchParams();
    if (currentQ) p.set('q', currentQ);
    if (currentFilter && currentFilter !== 'all') p.set('filter', currentFilter);
    if (currentChat) p.set('chat', currentChat);
    if (currentDate) p.set('date', currentDate);

    const res = await fetch('/rows?' + p.toString(), { signal: currentAbortController.signal });
    const html = await res.text();
    const tb = document.getElementById('tb');
    if (force || tb.innerHTML !== html) {
      tb.innerHTML = html;
      applyCollapsedState();
      showingOrig.forEach(id => {
        const el = document.getElementById('text-' + id);
        const btn = document.getElementById('btn-' + id);
        if(el && btn){
          el.textContent = el.getAttribute('data-orig') || '';
          el.classList.add('is-orig');
          btn.classList.add('active');
          btn.textContent = 'после изменения';
        }
      });
      if(currentAudio && !currentAudio.paused){
        document.querySelectorAll('.voice-box').forEach(box => {
          const btn = box.querySelector('.voice-btn');
          if(btn && btn.getAttribute('data-src') === currentAudioSrc){
            btn.textContent = '⏸';
          }
        });
      }
      requestAnimationFrame(() => applyAppleEmojis(tb));
    }
  } catch(e) {
  } finally {
    isFetching = false;
  }
}

setInterval(() => fetchRows(false), 3000);

function toggleEdit(id, btn){
  const el = document.getElementById('text-' + id);
  if(!el) return;

  btn.style.pointerEvents = 'none';
  el.classList.add('diff-fade-out');

  setTimeout(() => {
    if(showingOrig.has(id)){
      showingOrig.delete(id);
      el.textContent = el.getAttribute('data-curr');
      el.classList.remove('is-orig');
      btn.classList.remove('active');
      btn.textContent = 'до изменения';
    } else {
      showingOrig.add(id);
      el.textContent = el.getAttribute('data-orig') || '(неизвестно)';
      el.classList.add('is-orig');
      btn.classList.add('active');
      btn.textContent = 'после изменения';
    }
    applyAppleEmojis(el);

    el.classList.remove('diff-fade-out');
    el.classList.add('diff-fade-in');

    setTimeout(() => {
      el.classList.remove('diff-fade-in');
      btn.style.pointerEvents = 'auto';
    }, 220);
  }, 160);
}

function showMedia(url, type, e){
  if(e){ e.preventDefault(); e.stopPropagation(); }
  const modal = document.getElementById('modal');
  let content = '';
  if (type === 'video') {
    content = '<video src="' + url + '" controls autoplay playsinline style="max-width:90vw;max-height:85vh;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,0.8);background:#000;outline:none"></video>';
  } else {
    content = '<img src="' + url + '" alt="media" style="max-width:90vw;max-height:85vh;object-fit:contain;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,0.8)">';
  }
  modal.innerHTML = '<div style="position:relative;display:flex;justify-content:center;align-items:center" onclick="event.stopPropagation()">' + content + '</div>';
  modal.style.display = 'flex';
}

function closeModal(){
  const modal = document.getElementById('modal');
  modal.style.display = 'none';
  modal.innerHTML = '';
}
document.addEventListener('keydown', e=>{if(e.key==='Escape')closeModal();});

function togglePlay(btn, src) {
  const box = btn.closest('.voice-box');
  if (currentAudio && currentAudioSrc === src) {
    if (currentAudio.paused) {
      currentAudio.play();
      btn.textContent = '⏸';
    } else {
      currentAudio.pause();
      btn.textContent = '▶';
    }
    return;
  }
  if (currentAudio) {
    currentAudio.pause();
    document.querySelectorAll('.voice-btn').forEach(b => b.textContent = '▶');
  }
  currentAudio = new Audio(src);
  currentAudioSrc = src;
  btn.setAttribute('data-src', src);
  btn.textContent = '⏸';

  currentAudio.ontimeupdate = () => {
    const b = btn.closest('.voice-box');
    if (!b) return;
    const prog = b.querySelector('.voice-prog');
    const timeSpan = b.querySelector('.voice-time');
    if (currentAudio.duration && prog) {
      prog.style.width = ((currentAudio.currentTime / currentAudio.duration) * 100) + '%';
    }
    if (timeSpan) {
      const s = Math.floor(currentAudio.currentTime);
      timeSpan.textContent = Math.floor(s / 60) + ':' + (s % 60).toString().padStart(2, '0');
    }
  };
  currentAudio.onended = () => {
    btn.textContent = '▶';
    const b = btn.closest('.voice-box');
    if (!b) return;
    const prog = b.querySelector('.voice-prog');
    if (prog) prog.style.width = '0%';
  };
  currentAudio.play().catch(() => {});
}

function seekVoice(bar, e) {
  if (!currentAudio) return;
  const rect = bar.getBoundingClientRect();
  const clickPos = (e.clientX - rect.left) / rect.width;
  if (currentAudio.duration) {
    currentAudio.currentTime = clickPos * currentAudio.duration;
  }
}
</script></body></html>"""


def render_media_badge(media_file):
    if not media_file:
        return ""
    ext = os.path.splitext(media_file)[1].lower()
    if ext in [".ogg", ".mp3", ".wav", ".m4a"]:
        return (
            f'<div class="voice-box" onclick="event.stopPropagation()">'
            f'<button class="voice-btn" data-src="/media/{media_file}" onclick="togglePlay(this, \'/media/{media_file}\')">▶</button>'
            f'<div class="voice-bar" onclick="seekVoice(this, event)">'
            f'<div class="voice-prog"></div>'
            f'</div>'
            f'<span class="voice-time">0:00</span>'
            f'</div>'
        )
    elif ext in [".mp4", ".webm", ".mov"]:
        return (
            f'<video class="media-thumb" src="/media/{media_file}" autoplay loop muted playsinline '
            f'onclick="showMedia(\'/media/{media_file}\', \'video\', event)"></video>'
        )
    else:
        return (
            f'<img class="media-thumb" src="/media/{media_file}" alt="img" '
            f'onclick="showMedia(\'/media/{media_file}\', \'image\', event)">'
        )


def rows_html(q="", filter_type="all", chat_id=None, date_str="", limit=300):
    sql = """
        SELECT id, msg_id, chat_id, chat_title, chat_username, text, date, avatar, 
               reply_text, media_file, is_deleted, orig_text, is_edited, reply_media_file 
        FROM logs
        WHERE 1=1
    """
    args = []
    if q:
        date_q = None
        parts = q.strip().split(".")
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            d_p, m_p, y_p = parts
            if len(y_p) == 4 and len(d_p) <= 2 and len(m_p) <= 2:
                date_q = f"{y_p}-{int(m_p):02d}-{int(d_p):02d}"
        elif len(parts) == 2 and all(p.isdigit() for p in parts):
            d_p, m_p = parts
            if len(d_p) <= 2 and len(m_p) <= 2:
                date_q = f"-{int(m_p):02d}-{int(d_p):02d}"

        if date_q:
            sql += " AND (text LIKE ? OR chat_title LIKE ? OR reply_text LIKE ? OR orig_text LIKE ? OR chat_username LIKE ? OR date LIKE ?)"
            args.extend([f"%{q}%"] * 5 + [f"%{date_q}%"])
        else:
            sql += " AND (text LIKE ? OR chat_title LIKE ? OR reply_text LIKE ? OR orig_text LIKE ? OR chat_username LIKE ? OR date LIKE ?)"
            args.extend([f"%{q}%"] * 5 + [f"%{q}%"])

    if filter_type == "deleted":
        sql += " AND is_deleted = 1"
    elif filter_type == "edited":
        sql += " AND is_edited = 1"
    elif filter_type == "media":
        sql += " AND ((media_file IS NOT NULL AND media_file != '') OR (reply_media_file IS NOT NULL AND reply_media_file != ''))"

    if chat_id is not None:
        sql += " AND chat_id = ?"
        args.append(chat_id)

    if date_str:
        now_local = datetime.now().astimezone()
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        if date_str == "today":
            dt_start = today_start
            dt_end = today_start + timedelta(days=1)
            utc_start = dt_start.astimezone(timezone.utc).isoformat()
            utc_end = dt_end.astimezone(timezone.utc).isoformat()
            sql += " AND date >= ? AND date < ?"
            args.extend([utc_start, utc_end])
        elif date_str == "yesterday":
            dt_start = today_start - timedelta(days=1)
            dt_end = today_start
            utc_start = dt_start.astimezone(timezone.utc).isoformat()
            utc_end = dt_end.astimezone(timezone.utc).isoformat()
            sql += " AND date >= ? AND date < ?"
            args.extend([utc_start, utc_end])
        else:
            try:
                dt_local = datetime.strptime(date_str, "%Y-%m-%d").astimezone()
                dt_local_end = dt_local + timedelta(days=1)
                utc_start = dt_local.astimezone(timezone.utc).isoformat()
                utc_end = dt_local_end.astimezone(timezone.utc).isoformat()
                sql += " AND date >= ? AND date < ?"
                args.extend([utc_start, utc_end])
            except Exception:
                sql += " AND date LIKE ?"
                args.append(f"{date_str}%")

    out = []

    # Чёткая векторная SVG-стрелка
    arrow_svg = (
        '<svg class="reply-arrow" viewBox="0 0 16 16" width="13" height="13" fill="none">'
        '<path d="M3 8h10M9 4l4 4-4 4" stroke="#606060" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg>'
    )
    chevron_svg = (
        '<svg class="chevron-svg" viewBox="0 0 16 16" width="12" height="12" fill="none">'
        '<path d="M3.5 6l4.5 4.5 4.5-4.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg>'
    )

    last_year = None
    last_month = None
    last_day = None
    row_idx = 0

    for i, _, cid, title, uname, text, date, av, rep_text, media, is_del, orig_text, is_edit, rep_media in db.execute(
        sql + " ORDER BY id DESC LIMIT ?", args + [limit]
    ):
        d = datetime.fromisoformat(date).astimezone()
        curr_year = d.year
        curr_month = d.month
        curr_day = (d.year, d.month, d.day)

        # 1. Разделитель года
        if curr_year != last_year:
            out.append(
                f'<tr class="sep-row sep-year-row" data-sep-year="{curr_year}"><td colspan="4">'
                f'<div class="sep-year" onclick="toggleCollapseYear({curr_year}, this, event)" title="Свернуть / развернуть {curr_year} год">'
                f'<span>{curr_year}</span>{chevron_svg}'
                f'</div>'
                f'</td></tr>'
            )
            last_year = curr_year
            last_month = None
            last_day = None

        # 2. Разделитель месяца
        if curr_month != last_month:
            m_name = MONTHS_NOM.get(curr_month, "")
            month_key = f"{curr_year}-{curr_month:02d}"
            out.append(
                f'<tr class="sep-row sep-month-row" data-sep-year="{curr_year}" data-sep-month="{month_key}"><td colspan="4">'
                f'<div class="sep-month" onclick="toggleCollapseMonth(\'{month_key}\', this, event)" title="Свернуть / развернуть {m_name}">'
                f'<span>{m_name}</span>{chevron_svg}'
                f'</div>'
                f'</td></tr>'
            )
            last_month = curr_month
            last_day = None

        # 3. Разделитель дня
        if curr_day != last_day:
            day_label = f"{d.day} {MONTHS_GEN.get(curr_month, '')}"
            iso_day = f"{d.year:04d}-{d.month:02d}-{d.day:02d}"
            month_key = f"{curr_year}-{curr_month:02d}"
            day_label_attr = html.escape(day_label.replace("'", "\\'"), True)
            out.append(
                f'<tr class="sep-row sep-day-row" data-sep-year="{curr_year}" data-sep-month="{month_key}" data-sep-day="{iso_day}"><td colspan="4">'
                f'<div class="sep-day">'
                f'<div class="sep-day-badge">'
                f'<span class="sep-day-text" onclick="filterDate(\'{iso_day}\', \'{day_label_attr}\')" title="Показать только сообщения за {day_label}">{day_label}</span>'
                f'<button type="button" class="sep-day-toggle" onclick="toggleCollapseDay(\'{iso_day}\', this, event)" title="Свернуть / развернуть {day_label}">{chevron_svg}</button>'
                f'</div>'
                f'<div class="sep-day-line"></div>'
                f'</div>'
                f'</td></tr>'
            )
            last_day = curr_day

        title_clean = title or "Без имени"
        title_esc = html.escape(title_clean)
        title_attr = html.escape(title_clean.replace("'", "\\'"), True)

        ava = f'<img src="/avatars/{av}">' if av else f"<i>{html.escape(title_clean[:1].upper())}</i>"
        ava_clickable = f'<a href="javascript:void(0)" class="ava-link" onclick="filterChat({cid}, \'{title_attr}\')" title="Показать только этот чат">{ava}</a>'
        chat_link = f'<a href="javascript:void(0)" onclick="filterChat({cid}, \'{title_attr}\')" title="Показать только этот чат">{title_esc}</a>'
        un = f"<span>@{html.escape(uname)}</span>" if uname else ""

        curr_attr = html.escape(text or "", True)
        orig_attr = html.escape(orig_text or "", True)
        text_span = f'<span id="text-{i}" class="reply-text" data-curr="{curr_attr}" data-orig="{orig_attr}">{html.escape(text)}</span>'

        msg_media_html = render_media_badge(media)

        content_html = ""
        if rep_text or rep_media:
            rep_media_badge = render_media_badge(rep_media)
            rep_parts = []
            if rep_media_badge:
                rep_parts.append(rep_media_badge)
            if rep_text and rep_text != "[медиа]":
                rep_parts.append(f'<span class="reply-orig" title="{html.escape(rep_text)}">{html.escape(rep_text)}</span>')

            orig_side = " ".join(rep_parts)
            reply_side = " ".join(filter(None, [text_span if text else "", msg_media_html]))

            content_html = (
                f'<div class="reply-wrap">'
                f'{orig_side}'
                f'{arrow_svg}'
                f'{reply_side}'
                f'</div>'
            )
        else:
            content_html = " ".join(filter(None, [text_span if text else "", msg_media_html]))

        # Кнопка истории изменений (только если текст реально изменился)
        if is_edit and orig_text is not None and (orig_text or "") != (text or ""):
            content_html += (
                f' <span class="edit-badge">ред.</span>'
                f'<button id="btn-{i}" class="diff-btn" onclick="toggleEdit({i}, this)">до изменения</button>'
            )

        if is_del:
            content_html += ' <span class="del-badge">удалено</span>'

        row_cls = "row-odd" if row_idx % 2 == 0 else "row-even"
        row_idx += 1
        month_key = f"{curr_year}-{curr_month:02d}"
        iso_day = f"{d.year:04d}-{d.month:02d}-{d.day:02d}"

        out.append(
            f"<tr class='{row_cls} msg-row' data-msg-year='{curr_year}' data-msg-month='{month_key}' data-msg-day='{iso_day}'>"
            f"<td class=n>{i}</td><td class=t>{content_html}</td>"
            f"<td class=d title='{d:%d.%m.%Y %H:%M}'>{d:%H:%M}</td>"
            f"<td class=s><div>{ava_clickable}{chat_link}{un}</div></td></tr>"
        )

    if not out:
        return '<tr class="empty-row"><td colspan="4">Сообщений не найдено</td></tr>'

    return "".join(out)


@app.get("/", response_class=HTMLResponse)
async def index(q: str = "", filter: str = "all", chat: str = "", date: str = ""):
    cid = int(chat) if chat.lstrip("-").isdigit() else None
    return PAGE.replace("%ROWS%", rows_html(q=q, filter_type=filter, chat_id=cid, date_str=date))


@app.get("/rows", response_class=HTMLResponse)
async def rows(q: str = "", filter: str = "all", chat: str = "", date: str = ""):
    cid = int(chat) if chat.lstrip("-").isdigit() else None
    return rows_html(q=q, filter_type=filter, chat_id=cid, date_str=date)


# ---------- Запуск ----------
async def run_telethon():
    while True:
        try:
            if not client.is_connected():
                await client.connect()
            await client.run_until_disconnected()
        except Exception as e:
            print(f"Telegram reconnecting in 5s... ({e})")
            await asyncio.sleep(5)


async def main():
    global my_id
    await client.start()
    me = await client.get_me()
    my_id = me.id
    print(f"Logged in as: {me.first_name} (@{me.username or me.id}) [ID: {my_id}]")
    print(f"Web UI: http://{HOST}:{PORT}")
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    await asyncio.gather(server.serve(), run_telethon())


if __name__ == "__main__":
    asyncio.run(main())
