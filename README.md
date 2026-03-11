# SQL Telegram Bot (Qwen API)

Телеграм-бот, который принимает текстовое задание или SQL и возвращает готовый SQL-код.
Для генерации ответов используется `Qwen` через официальный API, без локального браузера.

## 1) Подготовка

1. Создайте бота через `@BotFather` и получите `TELEGRAM_BOT_TOKEN`.
2. Установите Python 3.10+.

## 2) Установка

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 3) Настройка

Создайте `.env` по примеру `.env.example` и заполните:

- `TELEGRAM_BOT_TOKEN`
- `QWEN_API_KEY`
- `QWEN_BASE_URL` (по умолчанию `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`)
- `QWEN_MODEL` (по умолчанию `qwen3.5-plus`)
- `QWEN_HTTP_TIMEOUT_SECONDS` (таймаут HTTP-запроса к модели)
- `QWEN_MAX_RETRIES` (повторы при временных ошибках API)
- `QWEN_RETRY_BACKOFF_SECONDS` (базовая пауза между повторами)

Общее:

- `POLL_TIMEOUT_SECONDS` (по умолчанию `40`)

Важно: не оставляйте значения вида `your_..._here`, иначе бот не запустится.

## 4) Запуск

```bash
python bot.py
```

После запуска откройте бота в Telegram и отправьте:

- описание задачи (например, "Сделай запрос топ-10 клиентов по выручке");
- или ваш SQL для исправления/улучшения.

Бот отвечает финальным SQL-кодом.

## Как это работает

1. Бот получает сообщение из Telegram.
2. Отправляет его в `Qwen API` как `system + user` messages.
3. Возвращает пользователю только финальный SQL.

Локальный браузер, Playwright и ручной логин больше не нужны.
