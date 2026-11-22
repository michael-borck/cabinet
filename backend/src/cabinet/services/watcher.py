"""Folder watching (Phase 1b, design §4).

Filesystem events are hints, never truth: the watcher marks sources dirty
and an asyncio loop turns that into debounced, single-flight quick-sync
jobs through the same scanner/pipeline reconcile used by manual scans.
Launch and periodic reconciles are the safety nets for missed events.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from cabinet.config import Settings, get_settings
from cabinet.db.models import Source
from cabinet.db.repositories import SourceRepository
from cabinet.services.sync import create_and_run_sync
from cabinet.utils.volumes import describe_source_root, find_rebound_path

logger = logging.getLogger(__name__)

# Editor/browser temp artifacts that should not, by themselves, dirty a
# source (the scanner's exclusions remain the real filter).
_IGNORED_SUFFIXES = (".crdownload", ".part", ".tmp", ".download", ".swp", ".swo")
_IGNORED_PREFIXES = ("~$",)
# How often the watcher re-reads which sources are watch-enabled, so source
# create/update/delete takes effect without router coupling.
_REFRESH_INTERVAL_SECONDS = 5.0

SyncRunner = Callable[[int, str], Awaitable[None]]


class _DirtyMarkingHandler(FileSystemEventHandler):
    """Watchdog handler that only records 'source N saw activity'."""

    def __init__(self, service: WatcherService, source_id: int) -> None:
        self._service = service
        self._source_id = source_id

    def on_any_event(self, event: FileSystemEvent) -> None:
        path = getattr(event, "dest_path", "") or event.src_path
        name = os.path.basename(str(path))
        if name.startswith(_IGNORED_PREFIXES) or name.endswith(_IGNORED_SUFFIXES):
            return
        if name.startswith("."):
            return
        self._service.mark_dirty(self._source_id)


class WatcherService:
    """Owns the watchdog observer and the debounce/refresh loops."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings | None = None,
        sync_runner: SyncRunner | None = None,
        use_polling_observer: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or get_settings()
        self._sync_runner: SyncRunner = sync_runner or self._default_sync
        self._use_polling = use_polling_observer
        self._observer = None
        self._watches: dict[int, object] = {}  # source_id -> watchdog watch
        self._watch_paths: dict[int, str] = {}
        # source_id -> [first_dirty, last_dirty, trigger] (monotonic times)
        self._dirty: dict[int, tuple[float, float, str]] = {}
        self._running: set[int] = set()
        self._dirty_again: set[int] = set()
        self._tasks: list[asyncio.Task] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_refresh = 0.0
        self._started_at: float | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self._settings.watch_enabled:
            logger.info("Folder watching disabled by CABINET_WATCH_ENABLED")
            return
        self._loop = asyncio.get_running_loop()
        self._started_at = time.monotonic()
        self._observer = PollingObserver() if self._use_polling else Observer()
        await self.refresh()
        self._observer.start()
        self.start_background_loops()
        # Reconcile-on-launch: catch everything that changed while the app
        # was closed (events cannot cover that window by definition).
        for source_id in list(self._watches):
            self.mark_dirty(source_id, trigger="launch", immediate=True)
        logger.info("Folder watcher started (%d source(s))", len(self._watches))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        if self._observer is not None:
            self._observer.stop()
            await asyncio.to_thread(self._observer.join)
            self._observer = None

    def start_background_loops(self) -> None:
        """Start debounce/refresh/periodic loops (also used by tests)."""
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        self._tasks.append(asyncio.create_task(self._debounce_loop()))
        self._tasks.append(asyncio.create_task(self._periodic_loop()))

    # ------------------------------------------------------------------
    # Dirty tracking (thread-safe entry point for watchdog callbacks)
    # ------------------------------------------------------------------

    def mark_dirty(
        self, source_id: int, *, trigger: str = "watch", immediate: bool = False
    ) -> None:
        if self._loop is not None and self._loop.is_running():
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # Called from the watchdog observer thread.
                self._loop.call_soon_threadsafe(
                    self._mark_dirty, source_id, trigger, immediate
                )
                return
        self._mark_dirty(source_id, trigger, immediate)

    def _mark_dirty(self, source_id: int, trigger: str, immediate: bool) -> None:
        now = time.monotonic()
        if source_id in self._running:
            self._dirty_again.add(source_id)
            return
        existing = self._dirty.get(source_id)
        first = existing[0] if existing else now
        last = now - self._settings.watch_debounce_seconds if immediate else now
        # A real filesystem event outranks launch/periodic bookkeeping.
        kept_trigger = existing[2] if existing and trigger != "watch" else trigger
        self._dirty[source_id] = (first, last, kept_trigger)

    # ------------------------------------------------------------------
    # Loops
    # ------------------------------------------------------------------

    async def _debounce_loop(self) -> None:
        tick = max(0.02, min(0.25, self._settings.watch_debounce_seconds / 4))
        while True:
            await asyncio.sleep(tick)
            self._tasks = [t for t in self._tasks if not t.done()]
            now = time.monotonic()
            if now - self._last_refresh >= _REFRESH_INTERVAL_SECONDS:
                self._last_refresh = now
                try:
                    await self.refresh()
                except Exception:
                    logger.exception("Watcher refresh failed")
            due = [
                source_id
                for source_id, (first, last, _t) in list(self._dirty.items())
                if now - last >= self._settings.watch_debounce_seconds
                or now - first >= self._settings.watch_max_wait_seconds
            ]
            for source_id in due:
                _first, _last, trigger = self._dirty.pop(source_id)
                if source_id in self._running:
                    self._dirty_again.add(source_id)
                    continue
                self._tasks.append(
                    asyncio.create_task(self._run_source(source_id, trigger))
                )

    async def _periodic_loop(self) -> None:
        interval = self._settings.watch_poll_interval_seconds
        if interval <= 0:
            return
        while True:
            await asyncio.sleep(interval)
            for source_id in list(self._watches):
                self.mark_dirty(source_id, trigger="periodic")

    async def _run_source(self, source_id: int, trigger: str) -> None:
        self._running.add(source_id)
        try:
            if await self._source_available(source_id):
                await self._sync_runner(source_id, trigger)
            else:
                logger.info("Source %d unavailable; skipping sync", source_id)
        except Exception:
            logger.exception("Watcher sync failed for source %d", source_id)
        finally:
            self._running.discard(source_id)
            if source_id in self._dirty_again:
                self._dirty_again.discard(source_id)
                self._mark_dirty(source_id, "watch", immediate=True)

    # ------------------------------------------------------------------
    # Source bookkeeping
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """Reconcile observer watches with watch-enabled sources in the DB.

        Also resolves each source's on-disk location (filling volume info
        and rebinding remounted drives) so availability checks and
        scheduling always act on a current path.
        """
        async with self._session_factory() as session:
            sources = await SourceRepository.list_watched(session)
            wanted: dict[int, Source] = {}
            for source in sources:
                await self._resolve_source(session, source)
                wanted[source.id] = source
            await session.commit()

        if self._observer is None:
            self._watch_paths = {s.id: s.path for s in wanted.values()}
            return

        # Remove watches that are gone or moved.
        for source_id, watch in list(self._watches.items()):
            source = wanted.get(source_id)
            if source is None or self._watch_paths.get(source_id) != source.path:
                try:
                    self._observer.unschedule(watch)
                except Exception:
                    logger.debug("Unscheduling watch for %d failed", source_id)
                self._watches.pop(source_id, None)
                self._watch_paths.pop(source_id, None)

        # Add watches that are new (or at a rebound path).
        for source in wanted.values():
            if source.id in self._watches:
                continue
            if not os.path.isdir(source.path):
                continue  # unavailable; a later refresh picks it up
            handler = _DirtyMarkingHandler(self, source.id)
            watch = self._observer.schedule(
                handler, source.path, recursive=source.recursive
            )
            self._watches[source.id] = watch
            self._watch_paths[source.id] = source.path

    async def _resolve_source(self, session: AsyncSession, source: Source) -> bool:
        """Fill/refresh volume info and rebind a remounted volume if needed.

        Returns True when the source root currently exists on disk.
        """
        if os.path.isdir(source.path):
            if source.volume_rel_path is None:
                volume_id, rel = describe_source_root(source.path)
                source.volume_id, source.volume_rel_path = volume_id, rel
            return True
        rebound = find_rebound_path(source.volume_id, source.volume_rel_path)
        if rebound is not None and rebound != source.path:
            logger.info("Rebound source %d: %s -> %s", source.id, source.path, rebound)
            source.path = rebound
            volume_id, rel = describe_source_root(rebound)
            source.volume_id, source.volume_rel_path = volume_id, rel
            return True
        return False

    async def _source_available(self, source_id: int) -> bool:
        async with self._session_factory() as session:
            source = await SourceRepository.get_by_id(session, source_id)
            if source is None or not source.watch_enabled:
                return False
            available = await self._resolve_source(session, source)
            await session.commit()
            return available

    async def _default_sync(self, source_id: int, trigger: str) -> None:
        await create_and_run_sync(
            self._session_factory,
            source_id,
            mode="quick_sync",
            trigger=trigger,
        )
