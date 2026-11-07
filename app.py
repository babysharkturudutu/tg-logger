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
body{margin:0;background:#0a0a0a;color:#fff;font-family:'Onest',system-ui,-apple-system,sans-serif;font-weight:500;font-size:15px;line-height:1.45;letter-spacing:-0.01em}
.w{max-width:1200px;margin:0 auto;padding:16px}

/* Панель фильтров */
.toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:14px}
.search-box{flex:1;min-width:240px}
.search-box input{width:100%;box-sizing:border-box;margin-bottom:0;padding:10px 14px;background:#141414;color:#fff;
 border:1px solid #222;border-radius:6px;font:inherit;outline:none;transition:border-color 0.15s}
.search-box input:focus{border-color:#444}
.date-input{background:#141414;color:#aaa;border:1px solid #222;border-radius:6px;padding:8px 12px;font:inherit;font-size:13px;outline:none;color-scheme:dark;height:42px;box-sizing:border-box;cursor:pointer;transition:border-color 0.15s,color 0.15s}
.date-input:hover,.date-input:focus{border-color:#444;color:#fff}
.pills{display:flex;align-items:center;flex-wrap:wrap;gap:6px}
.pill{background:#141414;color:#888;border:1px solid #222;border-radius:6px;padding:8px 14px;font:500 13px 'Onest',sans-serif;cursor:pointer;transition:all 0.15s;user-select:none;height:42px;box-sizing:border-box}
.pill:hover{background:#1a1a1a;color:#ccc;border-color:#333}
.pill.active{background:#262626;color:#fff;border-color:#4a4a4a}
.chat-tag{display:inline-flex;align-items:center;gap:6px;background:#142218;color:#4ade80;border:1px solid #22542e;border-radius:6px;padding:6px 12px;font-size:13px;font-weight:500;height:42px;box-sizing:border-box}
.chat-tag-close{cursor:pointer;opacity:0.75;font-weight:700;margin-left:4px;padding:0 2px}
.chat-tag-close:hover{opacity:1;color:#fff}
.reset-btn{background:none;border:none;color:#666;font:inherit;font-size:13px;cursor:pointer;text-decoration:underline;padding:6px 10px;height:42px}
.reset-btn:hover{color:#aaa}

table{width:100%;border-collapse:collapse}
tr:nth-child(odd){background:#0d0d0d} tr:nth-child(even){background:#141414} tr:hover{background:#202020}
td{padding:10px 14px;vertical-align:top}
.n{width:50px;color:#666;font-size:13px;font-weight:400}
.t{white-space:pre-wrap;word-break:break-word}
.d{width:110px;color:#9a9a9a;font-weight:400;white-space:nowrap}
.s{width:330px}.s div{display:flex;align-items:center;gap:8px}
.s img,.s i{width:22px;height:22px;flex:none;border-radius:50%;object-fit:cover;background:#333;
 font:600 12px/22px 'Onest',sans-serif;text-align:center;color:#aaa}
.s a{color:#bbb;text-decoration:none;font-weight:500;cursor:pointer;transition:color 0.15s}
.s a:hover{color:#fff;text-decoration:underline}
.s span{color:#777;font-weight:400}

/* Стили для реплаев (акцентный серый) */
.reply-wrap{display:inline-flex;align-items:center;flex-wrap:wrap;gap:6px}
.reply-orig{background:#181818;color:#8a8a8a;padding:2px 8px;border-radius:4px;font-size:13px;
 border-left:3px solid #5a5a5a;max-width:280px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.reply-arrow{display:inline-block;vertical-align:middle;margin:0 6px;flex-shrink:0;opacity:0.75;transition:opacity 0.15s,transform 0.15s}
.reply-wrap:hover .reply-arrow{opacity:1;transform:translateX(1px)}
.reply-text{color:#fff;transition:color 0.2s}
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
  <input type="date" id="dateInput" class="date-input" onchange="onDateChange(this.value)" title="Фильтр по дате">
  <div class="pills">
    <button class="pill active" data-filter="all" onclick="setFilter('all')">Все</button>
    <button class="pill" data-filter="deleted" onclick="setFilter('deleted')">Удалённые</button>
    <button class="pill" data-filter="edited" onclick="setFilter('edited')">Изменённые</button>
    <button class="pill" data-filter="media" onclick="setFilter('media')">С медиа</button>
  </div>
  <div id="chatFilterTag" class="chat-tag" style="display:none">
    <span>Чат:</span> <span id="chatFilterName"></span>
    <span class="chat-tag-close" onclick="clearChatFilter()" title="Сбросить фильтр чата">✕</span>
  </div>
  <button id="resetAllBtn" class="reset-btn" onclick="resetAllFilters()" style="display:none">Сбросить всё</button>
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

const showingOrig = new Set();
let currentAudio = null;
let currentAudioSrc = null;

// Инициализация полей
document.getElementById('searchInput').value = currentQ;
document.getElementById('dateInput').value = currentDate;
updatePillsUI();
updateChatTagUI();

function updatePillsUI() {
  document.querySelectorAll('.pill').forEach(p => {
    p.classList.toggle('active', p.getAttribute('data-filter') === currentFilter);
  });
}

function updateChatTagUI() {
  const tag = document.getElementById('chatFilterTag');
  const name = document.getElementById('chatFilterName');
  const resetBtn = document.getElementById('resetAllBtn');
  if (currentChat) {
    tag.style.display = 'inline-flex';
    name.textContent = currentChatTitle || ('ID: ' + currentChat);
  } else {
    tag.style.display = 'none';
  }
  const hasFilter = currentQ || currentFilter !== 'all' || currentChat || currentDate;
  resetBtn.style.display = hasFilter ? 'inline-block' : 'none';
}

function updateURL() {
  const p = new URLSearchParams();
  if (currentQ) p.set('q', currentQ);
  if (currentFilter && currentFilter !== 'all') p.set('filter', currentFilter);
  if (currentChat) {
    p.set('chat', currentChat);
    if (currentChatTitle) p.set('chat_title', currentChatTitle);
  }
  if (currentDate) p.set('date', currentDate);
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
  }, 250);
}

function onDateChange(val) {
  currentDate = val;
  updateURL();
  fetchRows(true);
}

function setFilter(f) {
  currentFilter = f;
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
  document.getElementById('searchInput').value = '';
  document.getElementById('dateInput').value = '';
  updatePillsUI();
  updateURL();
  fetchRows(true);
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

async function fetchRows(force = false) {
  try {
    const p = new URLSearchParams();
    if (currentQ) p.set('q', currentQ);
    if (currentFilter && currentFilter !== 'all') p.set('filter', currentFilter);
    if (currentChat) p.set('chat', currentChat);
    if (currentDate) p.set('date', currentDate);

    const res = await fetch('/rows?' + p.toString());
    const html = await res.text();
    const tb = document.getElementById('tb');
    if (force || tb.innerHTML !== html) {
      tb.innerHTML = html;
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
      applyAppleEmojis(tb);
    }
  } catch(e) {}
}

setInterval(() => fetchRows(false), 3000);

function toggleEdit(id, btn){
  const el = document.getElementById('text-' + id);
  if(!el) return;
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
        sql += " AND (text LIKE ? OR chat_title LIKE ? OR reply_text LIKE ? OR orig_text LIKE ? OR chat_username LIKE ?)"
        args.extend([f"%{q}%"] * 5)

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

    for i, _, cid, title, uname, text, date, av, rep_text, media, is_del, orig_text, is_edit, rep_media in db.execute(
        sql + " ORDER BY id DESC LIMIT ?", args + [limit]
    ):
        d = datetime.fromisoformat(date).astimezone()
        title_clean = title or "Без имени"
        title_esc = html.escape(title_clean)
        title_attr = html.escape(title_clean.replace("'", "\\'"), True)

        ava = f'<img src="/avatars/{av}">' if av else f"<i>{html.escape(title_clean[:1].upper())}</i>"
        ava_clickable = f'<a href="javascript:void(0)" onclick="filterChat({cid}, \'{title_attr}\')" title="Показать только этот чат">{ava}</a>'
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

        out.append(
            f"<tr><td class=n>{i}</td><td class=t>{content_html}</td>"
            f"<td class=d>{d:%d.%m.%Y}<br>{d:%H:%M}</td>"
            f"<td class=s><div>{ava_clickable}{chat_link}{un}</div></td></tr>"
        )

    if not out:
        return '<tr><td colspan="4" style="text-align:center;padding:40px 10px;color:#666;font-size:14px">Сообщений не найдено</td></tr>'

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
