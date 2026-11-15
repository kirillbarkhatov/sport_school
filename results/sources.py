"""Источники протокола: Google Таблица, XLSX на Drive, загруженный файл. Возвращают {лист: строки}."""

from __future__ import annotations

import datetime
import io
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from django.conf import settings

from .models import ResultsEvent

Sheets = dict[str, list[list[object]]]

_ID_IN_PATH = re.compile(r"/d/([a-zA-Z0-9_-]{20,})")
_PLAIN_ID = re.compile(r"^[a-zA-Z0-9_-]{20,}$")
SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
)


class SourceError(Exception):
    """Ошибка чтения источника (сообщение показывается пользователю)."""

    retryable = True


class SourceConfigError(SourceError):
    retryable = False


def extract_file_id(link_or_id: str) -> str:
    raw = (link_or_id or "").strip()
    if _PLAIN_ID.match(raw):
        return raw
    if match := _ID_IN_PATH.search(raw):
        return match.group(1)
    query_id = (parse_qs(urlparse(raw).query).get("id") or [""])[0]
    if _PLAIN_ID.match(query_id):
        return query_id
    raise SourceConfigError("Не удалось извлечь ID файла Google из ссылки")


def read_xlsx(content: bytes) -> Sheets:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        return {
            sheet.title: [
                ["" if value is None else value for value in row] for row in sheet.iter_rows(values_only=True)
            ]
            for sheet in workbook.worksheets
        }
    finally:
        workbook.close()


def _cell_value(value: object) -> object:
    """Время из Excel (time/timedelta) → секунды; числа как есть; остальное → строка."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, datetime.time):
        return round(value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6, 3)
    if isinstance(value, datetime.timedelta):
        return round(value.total_seconds(), 3)
    return "" if value is None else str(value).strip()


def _normalize(sheets: Sheets) -> Sheets:
    """Значения ячеек → строки/числа, обрезанные пустые хвосты (стабильный хэш снимка)."""
    result = {}
    for name, rows in sheets.items():
        normalized = []
        for row in rows:
            cells = [_cell_value(value) for value in row]
            while cells and cells[-1] in ("", None):
                cells.pop()
            normalized.append(cells)
        while normalized and not normalized[-1]:
            normalized.pop()
        result[name] = normalized
    return result


@dataclass
class GoogleSource:
    """Чтение Google Таблицы или XLSX с Drive. Объект живёт весь цикл воркера (кэш листов и ревизии файла)."""

    file_id: str
    credentials_file: str
    _sheets_api: object = field(default=None, init=False, repr=False)
    _drive_api: object = field(default=None, init=False, repr=False)
    _kind: str = field(default="", init=False)
    _titles: list[str] = field(default_factory=list, init=False)
    _revision: tuple = field(default=(), init=False)
    _cached: Sheets = field(default_factory=dict, init=False)
    _ticks: int = field(default=0, init=False)

    def _connect(self) -> None:
        if self._drive_api is not None:
            return
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover
            raise SourceConfigError("Не установлен google-api-python-client") from exc
        try:
            credentials = Credentials.from_service_account_file(self.credentials_file, scopes=SCOPES)
        except (OSError, ValueError) as exc:
            raise SourceConfigError(f"Не удалось прочитать ключ сервисного аккаунта: {exc}") from exc
        self._sheets_api = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        self._drive_api = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def _call(self, request):
        from googleapiclient.errors import HttpError

        try:
            return request.execute(num_retries=1)
        except HttpError as exc:
            status = getattr(exc.resp, "status", 0)
            if status in (403, 404):
                error = SourceConfigError(
                    "Нет доступа к файлу: выдайте сервисному аккаунту доступ «Читатель» или проверьте ссылку"
                )
                raise error from exc
            raise SourceError(f"Google API: HTTP {status}") from exc
        except OSError as exc:
            raise SourceError(f"Сеть: {exc}") from exc

    def _meta(self) -> dict:
        return self._call(
            self._drive_api.files().get(
                fileId=self.file_id, fields="mimeType,modifiedTime,md5Checksum,size", supportsAllDrives=True
            )
        )

    def fetch(self) -> Sheets:
        self._connect()
        self._ticks += 1
        if not self._kind:
            mime = self._meta().get("mimeType", "")
            self._kind = "sheet" if mime == "application/vnd.google-apps.spreadsheet" else "xlsx"
        if self._kind == "sheet":
            return self._fetch_sheet()
        return self._fetch_xlsx()

    def _fetch_sheet(self) -> Sheets:
        if not self._titles or self._ticks % 60 == 0:  # список листов обновляем раз в 60 опросов
            meta = self._call(
                self._sheets_api.spreadsheets().get(spreadsheetId=self.file_id, fields="sheets(properties(title))")
            )
            self._titles = [item["properties"]["title"] for item in meta.get("sheets", [])]
        response = self._call(
            self._sheets_api.spreadsheets()
            .values()
            .batchGet(
                spreadsheetId=self.file_id,
                ranges=[f"'{title}'!A:Z" for title in self._titles],
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="FORMATTED_STRING",
            )
        )
        ranges = response.get("valueRanges", [])
        return _normalize({title: data.get("values", []) for title, data in zip(self._titles, ranges, strict=False)})

    def _fetch_xlsx(self) -> Sheets:
        meta = self._meta()
        revision = (meta.get("modifiedTime"), meta.get("md5Checksum"), meta.get("size"))
        if revision == self._revision and self._cached:
            return self._cached
        content = self._call(self._drive_api.files().get_media(fileId=self.file_id, supportsAllDrives=True))
        self._cached = _normalize(read_xlsx(content))
        self._revision = revision
        return self._cached


@dataclass
class UploadSource:
    event: ResultsEvent

    def fetch(self) -> Sheets:
        if not self.event.source_file:
            raise SourceConfigError("Файл протокола не загружен")
        with self.event.source_file.open("rb") as handle:
            return _normalize(read_xlsx(handle.read()))


def build_source(event: ResultsEvent):
    """None — у ручного источника нечего опрашивать, данные вводятся в интерфейсе."""
    kind = event.source_kind
    if kind == ResultsEvent.SourceKind.MANUAL:
        return None
    if kind == ResultsEvent.SourceKind.UPLOAD:
        return UploadSource(event)
    credentials = getattr(settings, "RESULTS_GOOGLE_SERVICE_ACCOUNT_FILE", "")
    if not credentials:
        raise SourceConfigError("Не задан RESULTS_GOOGLE_SERVICE_ACCOUNT_FILE")
    return GoogleSource(file_id=extract_file_id(event.source_ref), credentials_file=credentials)
