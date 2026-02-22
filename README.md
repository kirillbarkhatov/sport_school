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
- `DOCS_ANALYZER_BATCH_SIZE` (default: `20`)
- `DOCS_ANALYZER_CONCURRENCY` (default: `5`)
- `DOCS_ANALYZER_MAX_URLS` (default: `20`)
- `DOCS_ANALYZER_SIGNED_URL_TTL_SEC` (default: `300`)
- `DOCS_ANALYZER_RETRY_MAX` (default: `4`)
- `DOCS_ANALYZER_RETRY_BACKOFF_SEC` (default: `15`)
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
1. Worker:
```bash
poetry run celery -A config worker --loglevel=info -Q docs_analysis,celery
```

2. Beat:
```bash
poetry run celery -A config beat --loglevel=info
```

3. Ручной запуск оркестратора анализа:
```bash
poetry run python manage.py enqueue_docs_analysis
```

4. Ручной запуск синка bucket -> Document:
```bash
poetry run python manage.py sync_bucket_documents
```

5. Ручной async-запуск синка bucket -> Document:
```bash
poetry run python manage.py sync_bucket_documents --async
```
