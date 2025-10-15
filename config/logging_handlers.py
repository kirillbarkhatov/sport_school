import logging

from django.conf import settings
from telegram import Bot


class TelegramLogHandler(logging.Handler):
    """Logging handler that forwards records to a Telegram chat."""

    max_message_length = 4096

    def __init__(self, chat_id: str, level=logging.NOTSET):
        super().__init__(level)
        self.chat_id = str(chat_id)
        self._bot: Bot | None = None

    def emit(self, record: logging.LogRecord) -> None:
        if not settings.BOT_TOKEN or not self.chat_id:
            return

        if self._bot is None:
            self._bot = Bot(token=settings.BOT_TOKEN)

        try:
            message = self.format(record)
            if not message:
                return

            if len(message) > self.max_message_length:
                message = f"{message[: self.max_message_length - 3]}..."

            self._bot.send_message(chat_id=self.chat_id, text=message)
        except Exception:
            self.handleError(record)
