import logging
import os
import time
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv


TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"
TELEGRAM_HTTP_TIMEOUT_SECONDS = 60
SQL_SYSTEM_PROMPT = (
    "You are an expert SQL assistant. "
    "Return only SQL code and short inline SQL comments when needed. "
    "If user provides existing SQL, improve or fix it and return the final SQL only. "
    "Do not include markdown fences."
)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    value_lower = value.lower()
    if "your_" in value_lower or "_here" in value_lower:
        raise RuntimeError(
            f"Environment variable {name} still has a placeholder value. "
            "Set a real token/key in .env."
        )
    return value


def telegram_request(token: str, method: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{TELEGRAM_API_BASE.format(token=token)}/{method}"
    response = requests.post(url, json=payload or {}, timeout=TELEGRAM_HTTP_TIMEOUT_SECONDS)
    try:
        data = response.json()
    except ValueError:
        response.raise_for_status()
        raise RuntimeError("Telegram API returned invalid JSON.")

    if response.status_code >= 400 or not data.get("ok"):
        description = data.get("description") or str(data)
        raise RuntimeError(f"Telegram API error {response.status_code}: {description}")

    return data


def ensure_polling_mode(token: str) -> None:
    telegram_request(token, "deleteWebhook", {"drop_pending_updates": False})
    logging.info("Telegram webhook cleared, polling mode enabled")


class QwenApiClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        http_timeout_seconds: int,
        max_retries: int,
        retry_backoff_seconds: int,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.http_timeout_seconds = http_timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self._session = requests.Session()

    def start(self) -> None:
        return None

    def close(self) -> None:
        self._session.close()

    @staticmethod
    def _extract_text(data: Dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(f"Unexpected Qwen API response: {data}")

        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            text = content.strip()
            if text:
                return text

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text_value = item.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    parts.append(text_value.strip())
            text = "\n".join(parts).strip()
            if text:
                return text

        raise RuntimeError(f"Qwen API returned an empty response: {data}")

    @staticmethod
    def _build_error_message(response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip() or f"HTTP {response.status_code}"

        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        return str(payload)

    def generate_sql(self, user_text: str) -> str:
        logging.info("Sending request to Qwen API, model=%s", self.model)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SQL_SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        endpoint = f"{self.base_url}/chat/completions"

        for attempt in range(self.max_retries + 1):
            try:
                response = self._session.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.http_timeout_seconds,
                )
            except requests.exceptions.RequestException as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(f"Qwen API request failed: {exc}") from exc
                sleep_seconds = self.retry_backoff_seconds * (2 ** attempt)
                logging.warning(
                    "Qwen API request failed, retrying in %s seconds (%s/%s)",
                    sleep_seconds,
                    attempt + 1,
                    self.max_retries,
                )
                time.sleep(sleep_seconds)
                continue

            if response.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                sleep_seconds = self.retry_backoff_seconds * (2 ** attempt)
                logging.warning(
                    "Qwen API returned %s, retrying in %s seconds (%s/%s)",
                    response.status_code,
                    sleep_seconds,
                    attempt + 1,
                    self.max_retries,
                )
                time.sleep(sleep_seconds)
                continue

            if response.status_code >= 400:
                raise RuntimeError(
                    f"Qwen API error {response.status_code}: {self._build_error_message(response)}"
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise RuntimeError("Qwen API returned invalid JSON.") from exc

            result = self._extract_text(data)
            logging.info("Qwen API response received successfully")
            return result

        raise RuntimeError("Qwen API request failed after retries.")


def build_welcome_text() -> str:
    return (
        "Привет! Я SQL-бот.\n"
        "Пришли задание или SQL-запрос, и я верну готовый SQL.\n\n"
        "Пример:\n"
        "Сделай запрос: вывести топ-10 клиентов по сумме заказов за 2025 год."
    )


def handle_message(qwen_client: QwenApiClient, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "")
    if not chat_id or not text:
        return None

    lowered = text.strip().lower()
    if lowered in {"/start", "/help"}:
        logging.info("Received command from chat_id=%s: %s", chat_id, lowered)
        return {"chat_id": chat_id, "text": build_welcome_text()}

    try:
        preview = text.replace("\r", " ").replace("\n", " ").strip()
        if len(preview) > 120:
            preview = f"{preview[:117]}..."
        logging.info("Received SQL task from chat_id=%s: %s", chat_id, preview)
        sql = qwen_client.generate_sql(text)
    except Exception as exc:
        logging.exception("SQL generation failed")
        return {
            "chat_id": chat_id,
            "text": f"Ошибка при генерации SQL: {exc}",
        }

    return {"chat_id": chat_id, "text": sql}


def run_bot() -> None:
    load_dotenv()
    setup_logging()

    telegram_token = get_env("TELEGRAM_BOT_TOKEN")
    poll_timeout = int(os.getenv("POLL_TIMEOUT_SECONDS", "40"))
    qwen_api_key = get_env("QWEN_API_KEY")
    qwen_base_url = os.getenv(
        "QWEN_BASE_URL",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    ).strip()
    qwen_model = os.getenv("QWEN_MODEL", "qwen3.5-plus").strip()
    qwen_http_timeout_seconds = int(os.getenv("QWEN_HTTP_TIMEOUT_SECONDS", "120"))
    qwen_max_retries = int(os.getenv("QWEN_MAX_RETRIES", "3"))
    qwen_retry_backoff_seconds = int(os.getenv("QWEN_RETRY_BACKOFF_SECONDS", "2"))
    qwen_client = QwenApiClient(
        api_key=qwen_api_key,
        base_url=qwen_base_url,
        model=qwen_model,
        http_timeout_seconds=qwen_http_timeout_seconds,
        max_retries=qwen_max_retries,
        retry_backoff_seconds=qwen_retry_backoff_seconds,
    )
    qwen_client.start()

    try:
        telegram_request(telegram_token, "getMe")
        ensure_polling_mode(telegram_token)
    except Exception as exc:
        raise RuntimeError(
            "Telegram bot startup check failed. Verify TELEGRAM_BOT_TOKEN and make sure "
            "the bot can switch to polling mode."
        ) from exc

    logging.info("Bot started with Qwen API backend, model=%s", qwen_model)

    offset = 0
    try:
        while True:
            try:
                updates = telegram_request(
                    telegram_token,
                    "getUpdates",
                    {"timeout": poll_timeout, "offset": offset + 1},
                )
                results = updates.get("result", [])
                if results:
                    logging.info("Received %s Telegram update(s)", len(results))
                for update in results:
                    offset = update.get("update_id", offset)
                    message = update.get("message")
                    if not message:
                        logging.info("Skipping non-message update_id=%s", offset)
                        continue
                    reply = handle_message(qwen_client, message)
                    if reply:
                        telegram_request(telegram_token, "sendMessage", reply)
                        logging.info("Reply sent to chat_id=%s", reply.get("chat_id"))
            except requests.exceptions.RequestException:
                logging.exception("Network error. Retrying...")
                time.sleep(3)
            except RuntimeError as exc:
                message = str(exc)
                if "Telegram API error 409" in message:
                    logging.error(
                        "Telegram returned 409 Conflict. Another bot instance is polling with the same token."
                    )
                    time.sleep(5)
                    continue
                logging.exception("API error. Retrying...")
                time.sleep(3)
            except Exception:
                logging.exception("Unexpected error. Retrying...")
                time.sleep(3)
    finally:
        qwen_client.close()


if __name__ == "__main__":
    run_bot()
