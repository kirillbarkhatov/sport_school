import logging

import httpx
from django.conf import settings


class TelegramLogHandler(logging.Handler):
    """Logging handler that forwards records to a Telegram chat."""

    max_message_length = 4096

    def __init__(self, chat_id: str, *, timeout: float = 5.0, level=logging.NOTSET):
        super().__init__(level)
        self.chat_id = str(chat_id)
        self.timeout = timeout

    def emit(self, record: logging.LogRecord) -> None:
        if not settings.BOT_TOKEN or not self.chat_id:
            return

        try:
            message = self.format(record)
            if not message:
                return

            if len(message) > self.max_message_length:
                message = f"{message[: self.max_message_length - 3]}..."

            response = httpx.post(
                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                json={"chat_id": self.chat_id, "text": message},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception:
            self.handleError(record)
