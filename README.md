# Sport School

## AI-анализ документов (Celery)

### Что добавлено
- Модель `DocumentAIAnalysis` (one-to-one к `school.Document`) для хранения результата AI-анализа.
- Поле `Document.updated_at` для отслеживания изменения документа.
- Массовая загрузка документов: `/documents/bulk-upload/` (только загрузка файлов, без привязки и метаданных).
- Табличный просмотр анализа с фильтрами: `/documents/analysis/`.
- Подписанный временный доступ к документу для AI: `/documents/signed/<document_id>/?token=...`.
- Celery pipeline:
  - `school.tasks.enqueue_documents_for_ai_analysis` (оркестратор),
  - `school.tasks.analyze_documents_batch_task` (батч-запрос к AI),
  - `school.tasks.sync_documents_from_storage_task` (синхронизация документов из storage/bucket).

### Новые env/settings
- `DOCS_ANALYZER_URL` (default: `http://192.168.0.4:8001`)
- `DOCS_ANALYZER_TIMEOUT_SEC` (default: `30`)
- `DOCS_ANALYZER_BATCH_SIZE` (default: `3`)
- `DOCS_ANALYZER_CONCURRENCY` (default: `2`)
- `DOCS_ANALYZER_MAX_URLS` (default: `3`)
- `DOCS_ANALYZER_SIGNED_URL_TTL_SEC` (default: `300`)
- `DOCS_ANALYZER_RETRY_MAX` (default: `1`)
- `DOCS_ANALYZER_RETRY_BACKOFF_SEC` (default: `15`)
- `DOCS_ANALYZER_RETRY_JITTER_SEC` (default: `5`)
- `DOCS_ANALYZER_TASK_RATE_LIMIT` (default: `10/m`)
- `DOCS_ANALYZER_DOCS_PER_MIN_LIMIT` (default: `120`)
- `DOCS_ANALYZER_TOKENS_PER_MIN_LIMIT` (default: `0`, выключен)
- `DOCS_ANALYZER_TOKENS_PER_DOC_ESTIMATE` (default: `3000`)
- `DOCS_ANALYZER_BUDGET_BACKOFF_SEC` (default: `20`)
- `DOCS_ANALYZER_BUDGET_BACKOFF_JITTER_SEC` (default: `5`)
- `CELERY_TASK_ACKS_LATE` (default: `True`)
- `CELERY_WORKER_PREFETCH_MULTIPLIER` (default: `1`)
- `CELERY_QUEUE_DOCS_ANALYSIS` (default: `docs_analysis`)
- `DOCS_STORAGE_SYNC_PREFIX` (default: `documents`)
- `DOCS_SYNC_ON_STARTUP` (default: `True`)

### Принципы работы URL
- В БД сохраняется постоянный URL (`source_persistent_url`), вычисляемый из `MEDIA_URL + file.name`.
- Перед вызовом AI генерируется свежий signed URL с коротким TTL.
- AI получает только signed URL.
- Сопоставление ответа AI выполняется по карте `signed_url -> document_id` внутри конкретного батча.

### Автосинхронизация из bucket
- При старте проекта (`runserver/gunicorn`) приложение ставит в очередь задачу `sync_documents_from_storage_task`.
- Задача рекурсивно читает `DOCS_STORAGE_SYNC_PREFIX` в storage и синхронизирует объекты в `school.Document` по ключу файла.
- Новые/обновлённые документы автоматически ставятся в AI-анализ.

### Команды запуска
1. Worker для AI-документов (строго последовательно):
```bash
poetry run celery -A config worker --loglevel=info -Q docs_analysis -c 1
```

2. Общий worker для остальных задач:
```bash
poetry run celery -A config worker --loglevel=info -Q celery -c 4
```

3. Beat:
```bash
poetry run celery -A config beat --loglevel=info
```

4. Ручной запуск оркестратора анализа:
```bash
poetry run python manage.py enqueue_docs_analysis
```

5. Ручной запуск синка bucket -> Document:
```bash
poetry run python manage.py sync_bucket_documents
```

6. Ручной async-запуск синка bucket -> Document:
```bash
poetry run python manage.py sync_bucket_documents --async
```
