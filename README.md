# СпецЦентр News Bot

Telegram-бот для публикации новостей об охране труда с модерацией через Claude AI.

## Как работает

1. **GitHub Actions** запускает `fetch` каждые 6 часов
2. Бот собирает новости с Минтруд, Роструд, МЧС, Минобрнауки
3. **Claude AI** пишет статью по каждой новости
4. Черновик приходит **владельцу в личку** с кнопками:
   - ✅ **Опубликовать** — пост уходит в канал
   - ✏️ **Редактировать** — напишите правки, Claude перепишет
5. Бот параллельно принимает **заявки от клиентов** через кнопку под постом

## Быстрый старт

### 1. Fork репозитория на GitHub

### 2. Добавить секреты (Settings → Secrets → Actions)

| Секрет | Значение |
|--------|----------|
| `BOT_TOKEN` | Токен от @BotFather |
| `CHANNEL_ID` | `-1003083288415` |
| `OWNER_CHAT_ID` | `313239413` |
| `BOT_USERNAME` | `speccnews_bot` |
| `ANTHROPIC_API_KEY` | Ключ с console.anthropic.com |

### 3. Включить Actions

GitHub → Actions → Enable workflows

### 4. Первый запуск вручную

Actions → SpecCentr News Bot → Run workflow

## Локальный запуск

```bash
pip install -r requirements.txt
cp .env.example .env
# Заполните .env

python speccentr_news_bot.py fetch   # отправить черновики
python speccentr_news_bot.py poll    # слушать кнопки
python speccentr_news_bot.py all     # всё вместе
```
