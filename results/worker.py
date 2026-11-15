"""Воркер трансляций: супервизор + поток на каждую трансляцию со статусом «Идёт».

Источник правды — БД: кнопки «Старт/Стоп» меняют статус, супервизор это замечает. После рестарта
процесса активные трансляции подхватываются автоматически, предыдущее состояние берётся из БД.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

from django.db import close_old_connections
from django.utils import timezone

from .models import ResultsEvent
from .services import TickResult, clear_error, ingest_snapshot, recompute, record_error, set_status
from .sources import SourceConfigError, SourceError, build_source
from .tasks import enqueue_publication

logger = logging.getLogger(__name__)

ACTIVE_WINDOW = timedelta(minutes=5)  # недавно были изменения → опрашиваем часто
CONFIG_ERROR_RETRY_SEC = 30
MAX_BACKOFF_SEC = 60


class EventRunner(threading.Thread):
    def __init__(self, event_id: int):
        super().__init__(name=f"results-event-{event_id}", daemon=True)
        self.event_id = event_id
        self.stop_event = threading.Event()
        self._source = None
        self._source_revision: int | None = None
        self._failures = 0

    def stop(self) -> None:
        self.stop_event.set()

    def tick(self) -> float:
        """Один опрос. Возвращает паузу до следующего."""
        event = ResultsEvent.objects.get(pk=self.event_id)
        if event.status != ResultsEvent.Status.RUNNING:
            self.stop()
            return 0
        try:
            if self._source is None or self._source_revision != event.source_revision:
                # новый файл/ссылка/разметка — источник пересоздаётся без перезапуска воркера
                self._source = build_source(event) or "manual"
                self._source_revision = event.source_revision
            if self._source == "manual":
                result = recompute(self.event_id)
            else:
                fetched_at = timezone.now()
                sheets = self._source.fetch()
                result = ingest_snapshot(
                    self.event_id, sheets, source_revision=self._source_revision, fetched_at=fetched_at
                )
                if result.stale:
                    return 0  # источник сменился во время чтения — сразу читаем новый
        except SourceConfigError as exc:
            self._source = None  # настройки могли поправить — пересоздадим источник
            record_error(self.event_id, str(exc))
            return CONFIG_ERROR_RETRY_SEC
        except SourceError as exc:
            self._failures += 1
            record_error(self.event_id, f"{exc} (попытка {self._failures})")
            return min(5 * 2 ** (self._failures - 1), MAX_BACKOFF_SEC)
        if self._failures or event.last_error:
            clear_error(self.event_id)
        self._failures = 0
        self._after_tick(event, result)
        return self._interval(event)

    def _after_tick(self, event: ResultsEvent, result: TickResult) -> None:
        if result.changed or result.focus_changed:
            enqueue_publication(self.event_id)
        if result.completed:
            event.refresh_from_db(fields=["last_changed_at", "finalize_timeout_sec"])
            quiet_for = timezone.now() - (event.last_changed_at or timezone.now())
            if quiet_for >= timedelta(seconds=event.finalize_timeout_sec):
                set_status(self.event_id, ResultsEvent.Status.FINISHED)
                self.stop()

    @staticmethod
    def _interval(event: ResultsEvent) -> float:
        event.refresh_from_db(fields=["last_changed_at", "poll_interval_sec", "idle_interval_sec"])
        recent = event.last_changed_at and timezone.now() - event.last_changed_at < ACTIVE_WINDOW
        return max(0.5, event.poll_interval_sec if recent else event.idle_interval_sec)

    def wait(self, pause: float) -> None:
        """Пауза между опросами; прерывается раньше, если источник сменили или трансляцию остановили."""
        deadline = time.monotonic() + pause
        while not self.stop_event.is_set():
            left = deadline - time.monotonic()
            if left <= 0:
                return
            if self.stop_event.wait(min(1.0, left)):
                return
            if self._source_revision is not None and pause > 1.5:
                revision = (
                    ResultsEvent.objects.filter(pk=self.event_id).values_list("source_revision", flat=True).first()
                )
                if revision is not None and revision != self._source_revision:
                    return

    def run(self) -> None:
        logger.info("results: трансляция %s запущена", self.event_id)
        while not self.stop_event.is_set():
            close_old_connections()
            try:
                pause = self.tick()
            except ResultsEvent.DoesNotExist:
                break
            except Exception as exc:  # не роняем поток из-за одной ошибки
                logger.exception("results: ошибка тика event=%s", self.event_id)
                try:
                    record_error(self.event_id, f"Внутренняя ошибка: {exc}")
                except Exception:
                    logger.exception("results: не удалось записать ошибку event=%s", self.event_id)
                pause = 10
            self.wait(pause)
        close_old_connections()
        logger.info("results: трансляция %s остановлена", self.event_id)


class Supervisor:
    def __init__(self, scan_interval: float = 3.0):
        self.scan_interval = scan_interval
        self.stop_event = threading.Event()
        self.runners: dict[int, EventRunner] = {}

    def scan(self) -> None:
        close_old_connections()
        active = set(ResultsEvent.objects.filter(status=ResultsEvent.Status.RUNNING).values_list("pk", flat=True))
        for event_id in active:
            runner = self.runners.get(event_id)
            if runner is None or not runner.is_alive():
                runner = EventRunner(event_id)
                self.runners[event_id] = runner
                runner.start()
        for event_id, runner in list(self.runners.items()):
            if event_id not in active:
                runner.stop()
            if not runner.is_alive():
                self.runners.pop(event_id, None)

    def run(self) -> None:
        logger.info("results: воркер запущен")
        while not self.stop_event.is_set():
            try:
                self.scan()
            except Exception:
                logger.exception("results: ошибка супервизора")
            self.stop_event.wait(self.scan_interval)
        for runner in self.runners.values():
            runner.stop()
        for runner in self.runners.values():
            runner.join(timeout=10)
        logger.info("results: воркер остановлен")
