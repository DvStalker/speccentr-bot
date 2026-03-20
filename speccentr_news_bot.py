import asyncio
import hashlib
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import List, Optional
from urllib.parse import quote_plus, unquote_plus, urlencode, urlparse

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

try:
    from bs4 import BeautifulSoup
except ImportError:
    raise SystemExit("pip install beautifulsoup4")

try:
    import lxml  # noqa: F401
except ImportError:
    raise SystemExit("pip install lxml")

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ==============================================================
# СПЕЦЦЕНТР NEWS BOT
# Режимы:
#   python speccentr_news_bot.py fetch  — найти новости через DuckDuckGo,
#                                         написать статью через Claude,
#                                         отправить владельцу на проверку
#   python speccentr_news_bot.py poll   — слушать кнопки и заявки клиентов
#   python speccentr_news_bot.py all    — fetch + poll одновременно
# ==============================================================

BOT_TOKEN     = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID    = os.getenv("CHANNEL_ID", "").strip()
OWNER_CHAT_ID = os.getenv("OWNER_CHAT_ID", "").strip()
BOT_USERNAME  = os.getenv("BOT_USERNAME", "").strip()
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

DB_PATH           = os.getenv("DB_PATH", "news_bot.db")
FETCH_TIMEOUT     = int(os.getenv("FETCH_TIMEOUT", "25"))
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "3"))
DRY_RUN           = os.getenv("DRY_RUN", "0") == "1"
COMPANY_NAME      = os.getenv("COMPANY_NAME", "СпецЦентр")
CONTACT_TEXT      = os.getenv(
    "CONTACT_TEXT",
    "Напишите нам — подберём программу обучения под ваш объект и должности.",
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("speccentr-bot")


# ==============================================================
# ДАННЫЕ
# ==============================================================
@dataclass
class NewsItem:
    source: str
    title: str
    url: str
    snippet: str
    raw_text: str = ""


# Поисковые запросы — по одному на каждую услугу
SEARCH_QUERIES = [
    "охрана труда изменения требования 2025 2026",
    "пожарная безопасность новые требования 2025 2026",
    "промышленная безопасность ростехнадзор 2025 2026",
    "работы на высоте требования изменения 2025",
    "СИЗ средства индивидуальной защиты требования 2025",
    "первая помощь на производстве требования 2025",
    "электробезопасность персонал требования 2025",
    "газоопасные работы требования 2025",
    "гражданская оборона ЧС требования 2025",
]

SERVICE_RULES = [
    {"service": "Обучение по охране труда (А, Б, В)", "tag": "#охрана_труда", "weight": 6,
     "keywords": ["охрана труда","управление охраной труда","профессиональных рисков",
                  "инструктаж","безопасным методам","программа а","программа б","программа в"]},
    {"service": "Обучение по применению СИЗ", "tag": "#сиз", "weight": 6,
     "keywords": ["сиз","средств индивидуальной защиты","применению сиз"]},
    {"service": "Обучение по оказанию первой помощи", "tag": "#первая_помощь", "weight": 6,
     "keywords": ["первая помощь","оказанию первой помощи","пострадавшим"]},
    {"service": "Пожарная безопасность", "tag": "#пожарная_безопасность", "weight": 6,
     "keywords": ["пожар","пожарной безопасности","противопожарного инструктажа","эвакуации","мчс"]},
    {"service": "Работы на высоте", "tag": "#работы_на_высоте", "weight": 7,
     "keywords": ["работе на высоте","работы на высоте","1 группа","2 группа","3 группа"]},
    {"service": "Работы в ограниченных и замкнутых пространствах", "tag": "#озп", "weight": 7,
     "keywords": ["ограниченном и замкнутом пространстве","озп","замкнутом пространстве"]},
    {"service": "Газоопасные работы", "tag": "#газоопасные_работы", "weight": 7,
     "keywords": ["газоопасных работ","газоопасные работы","газоопас"]},
    {"service": "Промышленная безопасность (А.1, Б.1, Б.3, Б.7–Б.11)", "tag": "#промбезопасность", "weight": 7,
     "keywords": ["промышленной безопасности","ростехнадзор","опасных производственных объектов",
                  "а.1","б.1","б.3","б.7","б.8","б.9","б.10","б.11"]},
    {"service": "Электробезопасность / электротехнический персонал", "tag": "#электробезопасность", "weight": 6,
     "keywords": ["электробезопас","электротехнического персонала","ii группу допуска"]},
    {"service": "Обучение по профессиям рабочих", "tag": "#рабочие_профессии", "weight": 5,
     "keywords": ["стропальщик","электрогазосварщик","машинист","крановщик","слесарь","монтажник"]},
    {"service": "Подъёмные сооружения и грузоподъёмная техника", "tag": "#подъемные_сооружения", "weight": 6,
     "keywords": ["подъемных сооружений","грузоподъем","кран","подъемника"]},
    {"service": "Гражданская оборона и защита от ЧС", "tag": "#го_и_чс", "weight": 5,
     "keywords": ["гражданской обороны","от чрезвычайных ситуаций","чс","го и чс"]},
    {"service": "Экологическая безопасность и отходы I–IV класса", "tag": "#экологическая_безопасность", "weight": 5,
     "keywords": ["экологической безопасности","опасными отходами","i-iv класса опасности"]},
]

NEGATIVE_KEYWORDS = ["пенсии","материнский капитал","социальные выплаты",
                     "демография","алименты","пособия","ипотек","стипенд"]

LEAD_TOPICS = {
    "lead_otruda":  "Охрана труда",
    "lead_fire":    "Пожарная безопасность",
    "lead_height":  "Работы на высоте",
    "lead_pb":      "Промышленная безопасность",
    "lead_pp":      "Первая помощь",
    "lead_ppe":     "СИЗ",
    "lead_workers": "Рабочие профессии",
    "lead_other":   "Другая программа",
}


# ==============================================================
# БД
# ==============================================================
def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS published_news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hash TEXT UNIQUE NOT NULL, source TEXT, title TEXT, url TEXT,
        service TEXT, published_at TEXT, created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pending_drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hash TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
        article TEXT NOT NULL, service TEXT, tag TEXT,
        source_url TEXT, source_title TEXT, tg_message_id INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL, tg_user_id TEXT, username TEXT,
        full_name TEXT, topic TEXT, source_post TEXT, company TEXT,
        contact_name TEXT, phone TEXT, comment TEXT, status TEXT DEFAULT 'new')""")
    conn.commit()
    return conn


def content_hash(title: str, url: str) -> str:
    return hashlib.sha256(f"{title}|{url}".encode()).hexdigest()

def already_posted(conn, h):
    return conn.execute("SELECT 1 FROM published_news WHERE hash=?", (h,)).fetchone() is not None

def already_pending(conn, h):
    return conn.execute("SELECT 1 FROM pending_drafts WHERE hash=?", (h,)).fetchone() is not None

def save_draft(conn, h, article, service, tag, url, title, msg_id):
    conn.execute(
        "INSERT OR IGNORE INTO pending_drafts "
        "(hash,created_at,article,service,tag,source_url,source_title,tg_message_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (h, datetime.now(timezone.utc).isoformat(), article, service, tag, url, title, msg_id))
    conn.commit()

def update_draft(conn, h, article, msg_id):
    conn.execute("UPDATE pending_drafts SET article=?,tg_message_id=? WHERE hash=?",
                 (article, msg_id, h))
    conn.commit()

def get_draft(conn, h):
    row = conn.execute(
        "SELECT hash,article,service,tag,source_url,source_title,tg_message_id "
        "FROM pending_drafts WHERE hash=?", (h,)).fetchone()
    if not row:
        return None
    return dict(zip(["hash","article","service","tag","source_url","source_title","tg_message_id"], row))

def delete_draft(conn, h):
    conn.execute("DELETE FROM pending_drafts WHERE hash=?", (h,))
    conn.commit()

def mark_posted(conn, h, title, url, service):
    conn.execute(
        "INSERT OR IGNORE INTO published_news "
        "(hash,source,title,url,service,published_at,created_at) VALUES (?,?,?,?,?,?,?)",
        (h,"канал",title,url,service,None,datetime.now(timezone.utc).isoformat()))
    conn.commit()

def save_lead(conn, tg_user_id, username, full_name, topic,
              company, contact_name, phone, comment):
    conn.execute(
        "INSERT INTO leads (created_at,tg_user_id,username,full_name,topic,"
        "source_post,company,contact_name,phone,comment) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), tg_user_id, username, full_name,
         topic, "", company, contact_name, phone, comment))
    conn.commit()


# ==============================================================
# УТИЛИТЫ
# ==============================================================
def normalize(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def he(text: str) -> str:
    return (text or "").replace("&","&amp;").replace("<","&lt;") \
                       .replace(">","&gt;").replace('"',"&quot;")

def detect_service(text: str) -> tuple[str, str, int]:
    txt = (text or "").lower()
    if any(bad in txt for bad in NEGATIVE_KEYWORDS):
        return "", "", 0
    best_service, best_tag, best_score = "", "#спеццентр", 0
    for rule in SERVICE_RULES:
        score = sum(rule["weight"] for kw in rule["keywords"] if kw in txt)
        if score > best_score:
            best_service, best_tag, best_score = rule["service"], rule["tag"], score
    if any(x in txt for x in ["вступает в силу","утверд","изменени","штраф","проверк","обязан"]):
        best_score += 2
    return best_service, best_tag, best_score

def build_deep_link(service: str) -> str:
    if not BOT_USERNAME:
        return ""
    payload = f"lead|{service}"[:64]
    return f"https://t.me/{BOT_USERNAME}?start={quote_plus(payload)}"

def get_lead_topic(service: str) -> str:
    s = service.lower()
    for kw, topic in [
        ("охране труда","Охрана труда"), ("пожар","Пожарная безопасность"),
        ("высоте","Работы на высоте"), ("промышлен","Промышленная безопасность"),
        ("первой помощи","Первая помощь"), ("сиз","СИЗ"),
        ("рабочих","Рабочие профессии"),
    ]:
        if kw in s:
            return topic
    return "Другая программа"


# ==============================================================
# ПОИСК ЧЕРЕЗ DUCKDUCKGO
# ==============================================================
async def ddg_search(query: str, max_results: int = 5) -> List[dict]:
    """
    Ищет через DuckDuckGo HTML (без API ключа).
    Возвращает список {"title": ..., "url": ..., "snippet": ...}
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    params = {"q": query, "kl": "ru-ru", "kad": "ru_RU"}
    url = "https://html.duckduckgo.com/html/?" + urlencode(params)

    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(url, timeout=timeout) as resp:
                resp.raise_for_status()
                html = await resp.text()
    except Exception as e:
        logger.warning("DuckDuckGo поиск '%s': %s", query, e)
        return []

    soup = BeautifulSoup(html, "lxml")
    results = []

    for result in soup.select(".result")[:max_results]:
        title_el = result.select_one(".result__title a")
        snippet_el = result.select_one(".result__snippet")
        if not title_el:
            continue

        title = normalize(title_el.get_text())
        snippet = normalize(snippet_el.get_text()) if snippet_el else ""

        # Извлекаем реальный URL из редиректа DDG
        href = title_el.get("href", "")
        if "uddg=" in href:
            from urllib.parse import parse_qs, urlparse as _urlparse
            qs = parse_qs(_urlparse(href).query)
            href = qs.get("uddg", [href])[0]
        elif href.startswith("/"):
            href = "https://duckduckgo.com" + href

        if not title or not href:
            continue

        results.append({"title": title, "url": href, "snippet": snippet})

    logger.info("DuckDuckGo '%s': найдено %d результатов", query, len(results))
    return results


async def fetch_article_text(url: str) -> str:
    """Скачивает страницу и извлекает основной текст."""
    headers = {"User-Agent": "Mozilla/5.0"}
    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=timeout,
                                   allow_redirects=True) as resp:
                if resp.status != 200:
                    return ""
                html = await resp.text(errors="replace")
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script","style","noscript","nav","footer","header"]):
            tag.decompose()
        return normalize(soup.get_text(" ", strip=True))[:3000]
    except Exception as e:
        logger.warning("Не удалось прочитать %s: %s", url, e)
        return ""


async def collect_news() -> List[tuple]:
    """
    Ищет новости через DuckDuckGo по каждому запросу,
    фильтрует по ключевым словам услуг.
    """
    seen_urls: set = set()
    results: List[tuple] = []  # (NewsItem, service, tag, score)

    for query in SEARCH_QUERIES:
        hits = await ddg_search(query, max_results=5)
        await asyncio.sleep(2)  # пауза между запросами чтобы не получить бан

        for hit in hits:
            url = hit["url"]
            title = hit["title"]
            snippet = hit["snippet"]

            if not url or not title or url in seen_urls:
                continue

            # Фильтруем мусорные домены
            domain = urlparse(url).netloc.lower()
            if any(skip in domain for skip in ["youtube","vk.com","ok.ru","instagram","facebook"]):
                continue

            seen_urls.add(url)

            # Быстрая проверка по заголовку и сниппету
            service, tag, score = detect_service(f"{title} {snippet}")
            if score < 5 or not service:
                continue

            # Читаем полный текст статьи
            raw_text = await fetch_article_text(url)
            if raw_text:
                # Пересчитываем score с полным текстом
                service, tag, score = detect_service(f"{title} {snippet} {raw_text}")
                if score < 5 or not service:
                    continue

            item = NewsItem(
                source=domain,
                title=title,
                url=url,
                snippet=snippet,
                raw_text=raw_text,
            )
            results.append((item, service, tag, score))

        if len(results) >= MAX_POSTS_PER_RUN * 3:
            break

    # Сортируем по score
    results.sort(key=lambda p: p[3], reverse=True)
    logger.info("Найдено релевантных новостей: %d", len(results))
    return results


# ==============================================================
# CLAUDE API
# ==============================================================
async def call_claude(prompt: str) -> str:
    if not ANTHROPIC_KEY:
        logger.warning("ANTHROPIC_API_KEY не задан — возвращаю заглушку")
        return prompt[:600]

    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers, json=payload, timeout=timeout,
        ) as resp:
            if resp.status != 200:
                logger.error("Claude API %s: %s", resp.status, await resp.text())
                return prompt[:600]
            data = await resp.json()
            return data["content"][0]["text"].strip()


async def generate_article(item: NewsItem, service: str) -> str:
    news_text = f"{item.title}\n\n{item.raw_text or item.snippet}"
    prompt = f"""Ты контент-менеджер учебного центра «СпецЦентр» \
(охрана труда, пожарная безопасность, промбезопасность).

Вот найденная новость:
---
{news_text[:3000]}
---

Напиши готовый пост для Telegram-канала. Требования:
- Длина 150–300 слов
- HTML-разметка Telegram: <b>, <i>, <a href="...">
- Структура:
  1. Цепляющий заголовок жирным
  2. Суть новости — что изменилось или вводится
  3. Кому важно (руководитель, специалист по ОТ, ответственный за безопасность)
  4. Что нужно сделать — конкретный призыв проверить/пройти обучение
  5. <a href="{he(item.url)}">Читать источник</a>
- Актуальная услуга: {service}
- В конце строго: 📩 <b>{he(CONTACT_TEXT)}</b>
- Без хэштегов (добавятся автоматически)
- Живо и по-деловому, без канцелярита

Верни ТОЛЬКО текст поста, без пояснений."""
    return await call_claude(prompt)


async def regenerate_article(draft_article: str, service: str,
                              source_url: str, editor_comment: str) -> str:
    prompt = f"""Ты контент-менеджер учебного центра «СпецЦентр».

Текущий черновик поста:
---
{draft_article[:3000]}
---

Редактор просит внести правки:
"{editor_comment}"

Напиши обновлённый пост с учётом правок. Требования:
- Длина 150–300 слов, HTML-разметка Telegram
- Услуга: {service}
- Ссылка: <a href="{he(source_url)}">Читать источник</a>
- В конце строго: 📩 <b>{he(CONTACT_TEXT)}</b>
- Без хэштегов

Верни ТОЛЬКО текст поста."""
    return await call_claude(prompt)


# ==============================================================
# КЛАВИАТУРЫ
# ==============================================================
def draft_keyboard(draft_hash: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub:{draft_hash}"),
        InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit:{draft_hash}"),
    ]])

def lead_keyboard(service: str) -> Optional[InlineKeyboardMarkup]:
    url = build_deep_link(service)
    if not url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Оставить заявку", url=url),
    ]])

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Охрана труда",              callback_data="lead_otruda")],
        [InlineKeyboardButton(text="Пожарная безопасность",     callback_data="lead_fire")],
        [InlineKeyboardButton(text="Работы на высоте",          callback_data="lead_height")],
        [InlineKeyboardButton(text="Промышленная безопасность", callback_data="lead_pb")],
        [InlineKeyboardButton(text="Первая помощь",             callback_data="lead_pp")],
        [InlineKeyboardButton(text="СИЗ",                       callback_data="lead_ppe")],
        [InlineKeyboardButton(text="Рабочие профессии",         callback_data="lead_workers")],
        [InlineKeyboardButton(text="Другая программа",          callback_data="lead_other")],
    ])


# ==============================================================
# РЕЖИМ FETCH
# ==============================================================
async def run_fetch() -> None:
    if not BOT_TOKEN or not OWNER_CHAT_ID:
        raise RuntimeError("Нужно задать BOT_TOKEN и OWNER_CHAT_ID")

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    conn = db_connect()

    try:
        logger.info("Ищу новости через DuckDuckGo...")
        scored = await collect_news()

        sent = 0
        for item, service, tag, _score in scored:
            h = content_hash(item.title, item.url)
            if already_posted(conn, h) or already_pending(conn, h):
                continue

            logger.info("Генерирую статью: %s", item.title)
            article = await generate_article(item, service)
            article_with_tag = f"{article}\n\n{tag} #спеццентр"

            preview = (
                f"📰 <b>Черновик — проверьте перед публикацией</b>\n"
                f"<b>Услуга:</b> {he(service)}\n"
                f"<b>Источник:</b> <a href=\"{he(item.url)}\">{he(item.source)}</a>\n\n"
                f"{'─' * 32}\n\n"
                f"{article_with_tag}"
            )

            if DRY_RUN:
                logger.info("DRY RUN:\n%s", preview)
                save_draft(conn, h, article_with_tag, service, tag,
                           item.url, item.title, 0)
            else:
                msg = await bot.send_message(
                    chat_id=OWNER_CHAT_ID,
                    text=preview,
                    reply_markup=draft_keyboard(h),
                )
                save_draft(conn, h, article_with_tag, service, tag,
                           item.url, item.title, msg.message_id)
                logger.info("Отправлено на проверку: %s (msg=%d)", item.title, msg.message_id)

            sent += 1
            if sent >= MAX_POSTS_PER_RUN:
                break
            await asyncio.sleep(1.5)

        logger.info("Черновиков отправлено: %d", sent)
    finally:
        await bot.session.close()
        conn.close()


# ==============================================================
# РЕЖИМ POLL
# ==============================================================
user_states: dict[int, dict] = {}


async def start_polling() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Нужно задать BOT_TOKEN")

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    conn = db_connect()

    # ---- Сброс вебхука при старте чтобы избежать Conflict ----
    await bot.delete_webhook(drop_pending_updates=True)

    @dp.callback_query(F.data.startswith("pub:"))
    async def cb_publish(callback: CallbackQuery) -> None:
        if str(callback.from_user.id) != OWNER_CHAT_ID:
            await callback.answer("Нет доступа.", show_alert=True)
            return
        h = callback.data.split(":", 1)[1]
        draft = get_draft(conn, h)
        if not draft:
            await callback.answer("Черновик не найден (уже опубликован?).", show_alert=True)
            return
        kb = lead_keyboard(draft["service"])
        await bot.send_message(chat_id=CHANNEL_ID, text=draft["article"], reply_markup=kb)
        mark_posted(conn, h, draft["source_title"], draft["source_url"], draft["service"])
        delete_draft(conn, h)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply("✅ Опубликовано в канал!")
        await callback.answer()
        logger.info("Опубликовано: %s", h[:12])

    @dp.callback_query(F.data.startswith("edit:"))
    async def cb_edit(callback: CallbackQuery) -> None:
        if str(callback.from_user.id) != OWNER_CHAT_ID:
            await callback.answer("Нет доступа.", show_alert=True)
            return
        h = callback.data.split(":", 1)[1]
        draft = get_draft(conn, h)
        if not draft:
            await callback.answer("Черновик не найден.", show_alert=True)
            return
        user_states[callback.from_user.id] = {"step": "editing", "draft_hash": h}
        await callback.message.reply(
            "✏️ Напишите правки одним сообщением.\n\n"
            "Например: <i>«сделай заголовок короче и добавь про штрафы»</i>\n\n"
            "Для отмены — /cancel"
        )
        await callback.answer()

    @dp.message(Command("cancel"))
    async def cmd_cancel(message: Message) -> None:
        user_states.pop(message.from_user.id, None)
        await message.answer("Отменено.")

    @dp.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        payload = unquote_plus(parts[1]) if len(parts) > 1 else ""
        service = payload.split("|", 1)[1] if payload.startswith("lead|") else ""
        user_states[message.from_user.id] = {
            "step": "wait_company",
            "topic": get_lead_topic(service) if service else "Другая программа",
            "service": service,
        }
        hello = f"Здравствуйте! Я бот {he(COMPANY_NAME)}.\n\nПомогу оставить заявку на обучение."
        if service:
            hello += f"\n\nТема: <b>{he(service)}</b>"
        hello += "\n\nНапишите название вашей компании."
        await message.answer(hello)

    @dp.message(Command("lead"))
    async def cmd_lead(message: Message) -> None:
        user_states[message.from_user.id] = {"step": "choose_topic"}
        await message.answer("Выберите направление:", reply_markup=main_menu_keyboard())

    @dp.callback_query(F.data.in_(LEAD_TOPICS.keys()))
    async def cb_lead_topic(callback: CallbackQuery) -> None:
        user_states[callback.from_user.id] = {
            "step": "wait_company",
            "topic": LEAD_TOPICS[callback.data],
            "service": LEAD_TOPICS[callback.data],
        }
        await callback.message.answer("Напишите название компании.")
        await callback.answer()

    @dp.message()
    async def text_handler(message: Message) -> None:
        user_id = message.from_user.id
        text = (message.text or "").strip()
        state = user_states.get(user_id, {})
        step = state.get("step", "")

        # Редактор — правки владельца
        if step == "editing" and str(user_id) == OWNER_CHAT_ID:
            h = state["draft_hash"]
            draft = get_draft(conn, h)
            if not draft:
                await message.answer("Черновик не найден — возможно уже опубликован.")
                user_states.pop(user_id, None)
                return
            await message.answer("⏳ Перегенерирую с вашими правками...")
            new_article = await regenerate_article(
                draft["article"], draft["service"], draft["source_url"], text
            )
            new_article_with_tag = f"{new_article}\n\n{draft['tag']} #спеццентр"
            preview = (
                f"📰 <b>Обновлённый черновик</b>\n"
                f"<b>Услуга:</b> {he(draft['service'])}\n\n"
                f"{'─' * 32}\n\n{new_article_with_tag}"
            )
            new_msg = await message.answer(preview, reply_markup=draft_keyboard(h))
            update_draft(conn, h, new_article_with_tag, new_msg.message_id)
            user_states.pop(user_id, None)
            return

        # Лид-форма
        if not step or step == "choose_topic":
            if not step:
                await message.answer(
                    "Здравствуйте! Чтобы оставить заявку — /lead "
                    "или перейдите по кнопке под постом в канале."
                )
            else:
                await message.answer("Выберите направление:", reply_markup=main_menu_keyboard())
            return

        if step == "wait_company":
            state["company"] = text
            state["step"] = "wait_name"
            await message.answer("Как к вам обращаться? Напишите имя контактного лица.")
        elif step == "wait_name":
            state["contact_name"] = text
            state["step"] = "wait_phone"
            await message.answer("Напишите телефон или WhatsApp.")
        elif step == "wait_phone":
            state["phone"] = text
            state["step"] = "wait_comment"
            await message.answer(
                "Кратко опишите задачу: сколько человек, программа, сроки.\n"
                "Если нечего добавить — напишите «-»."
            )
        elif step == "wait_comment":
            save_lead(conn, str(user_id),
                      message.from_user.username or "",
                      (message.from_user.full_name or "").strip(),
                      state.get("topic","Другая программа"),
                      state.get("company",""),
                      state.get("contact_name",""),
                      state.get("phone",""), text)
            lead_text = (
                f"📥 <b>Новая заявка</b>\n\n"
                f"<b>Тема:</b> {he(state.get('topic',''))}\n"
                f"<b>Компания:</b> {he(state.get('company',''))}\n"
                f"<b>Контакт:</b> {he(state.get('contact_name',''))}\n"
                f"<b>Телефон:</b> {he(state.get('phone',''))}\n"
                f"<b>Комментарий:</b> {he(text)}\n"
                f"<b>Telegram:</b> @{he(message.from_user.username or '')} / {user_id}"
            )
            if OWNER_CHAT_ID:
                await bot.send_message(chat_id=OWNER_CHAT_ID, text=lead_text)
            user_states.pop(user_id, None)
            await message.answer("Спасибо! Заявку получили. Свяжемся в ближайшее время.")

    logger.info("Polling запущен")
    await dp.start_polling(bot)


# ==============================================================
# ТОЧКА ВХОДА
# ==============================================================
async def main() -> None:
    if len(sys.argv) < 2:
        print(
            "Использование:\n"
            "  python speccentr_news_bot.py fetch  — найти новости и отправить черновики\n"
            "  python speccentr_news_bot.py poll   — слушать кнопки и заявки\n"
            "  python speccentr_news_bot.py all    — fetch + poll одновременно"
        )
        return
    mode = sys.argv[1].strip().lower()
    if mode == "fetch":
        await run_fetch()
    elif mode == "poll":
        await start_polling()
    elif mode == "all":
        await asyncio.gather(run_fetch(), start_polling())
    else:
        raise SystemExit(f"Неизвестный режим: {mode}")


if __name__ == "__main__":
    asyncio.run(main())
