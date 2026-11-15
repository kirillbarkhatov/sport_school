"""Настройки для pytest: не читают .env, не ходят во внешние сервисы."""

import os

# До импорта базовых настроек: отключаем .env и задаём безопасные значения.
os.environ["ENV_FILE"] = ""
_FORCED = {
    "BOT_TOKEN": "123456:TEST-TOKEN",  # фейковый: тесты мокают Bot, без токена код публикации пропускается
    "TELEGRAM_LOG_CHAT_ID": "",
    "STORAGE_BACKEND": "filesystem",
    "AI_ASSISTANT_ENABLED": "False",
    "DOCS_SYNC_ON_STARTUP": "False",
    "DOCS_AI_ORCHESTRATOR_ENABLED": "False",
    "TELEGRAM_DOC_IMPORT_ENABLED": "False",
}
os.environ.update(_FORCED)
_DEFAULTS = {
    "SECRET_KEY": "test-secret-key",
    "DEBUG": "False",
    "SITE_BASE_URL": "http://testserver",
    "POSTGRES_DB": "sport_school",
    "POSTGRES_USER": "sport_school",
    "POSTGRES_PASSWORD": "sport_school",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "ONLINE_RESULTS_WEBHOOK_SECRET": "test-webhook-secret",
}
for _key, _value in _DEFAULTS.items():
    os.environ.setdefault(_key, _value)

from config.settings import *  # noqa: E402, F403
from config.settings import BASE_DIR  # noqa: E402

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
CELERY_BROKER_URL = "memory://"
CELERY_TASK_ALWAYS_EAGER = False
CELERY_BEAT_SCHEDULE = {}
LOG_DIR = BASE_DIR / "logs"
SECURE_SSL_REDIRECT = False
ONLINE_RESULTS_TELEGRAM_PUBLISH_SYNC = True
RESULTS_TELEGRAM_SYNC = True
