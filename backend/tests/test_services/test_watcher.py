"""Tests for the Phase 1b folder watcher."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cabinet.config import Settings
from cabinet.db.models import Source
from cabinet.services import watcher as watcher_module
from cabinet.services.watcher import WatcherService
from cabinet.utils import volumes


def _factory(engine):
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


def _settings() -> Settings:
    return Settings(
        watch_enabled=True,
        watch_debounce_seconds=0.05,
        watch_max_wait_seconds=0.3,
        watch_poll_interval_seconds=0,
    )


async def _make_watched_source(session: AsyncSession, path: str) -> Source:
    source = Source(path=path, recursive=True, watch_enabled=True)
    session.add(source)
    await session.commit()
    return source


async def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


async def test_debounce_coalesces_event_storm(session, engine, tmp_path):
    """Many marks inside the debounce window become one sync."""
    source = await _make_watched_source(session, str(tmp_path))
    calls: list[tuple[int, str]] = []

    async def fake_sync(source_id: int, trigger: str) -> None:
        calls.append((source_id, trigger))

    watcher = WatcherService(_factory(engine), _settings(), sync_runner=fake_sync)
    watcher.start_background_loops()
    try:
        for _ in range(10):
            watcher.mark_dirty(source.id)
        assert await _wait_for(lambda: len(calls) >= 1)
        await asyncio.sleep(0.2)
        assert calls == [(source.id, "watch")]
    finally:
        await watcher.stop()


async def test_dirty_again_runs_exactly_one_followup(session, engine, tmp_path):
    """Events during a run produce one follow-up pass, not a storm."""
    source = await _make_watched_source(session, str(tmp_path))
    calls: list[tuple[int, str]] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_sync(source_id: int, trigger: str) -> None:
        calls.append((source_id, trigger))
        if len(calls) == 1:
            started.set()
            await release.wait()

    watcher = WatcherService(_factory(engine), _settings(), sync_runner=blocking_sync)
    watcher.start_background_loops()
    try:
        watcher.mark_dirty(source.id)
        assert await _wait_for(lambda: started.is_set())
        # Events while the first run is in flight.
        watcher.mark_dirty(source.id)
        watcher.mark_dirty(source.id)
        release.set()
        assert await _wait_for(lambda: len(calls) >= 2)
        await asyncio.sleep(0.25)
        assert len(calls) == 2  # no third run
    finally:
        await watcher.stop()


async def test_unavailable_source_is_skipped_not_synced(session, engine, tmp_path):
    """A vanished root (unplugged drive) never triggers a scan."""
    ghost = tmp_path / "not-mounted"
    source = await _make_watched_source(session, str(ghost))
    calls: list[tuple[int, str]] = []

    async def fake_sync(source_id: int, trigger: str) -> None:
        calls.append((source_id, trigger))

    watcher = WatcherService(_factory(engine), _settings(), sync_runner=fake_sync)
    watcher.start_background_loops()
    try:
        watcher.mark_dirty(source.id, immediate=True)
        await asyncio.sleep(0.4)
        assert calls == []
    finally:
        await watcher.stop()


async def test_rebound_source_becomes_available(session, engine, tmp_path, monkeypatch):
    """A source whose drive remounted elsewhere is rebound, then synced."""
    source = await _make_watched_source(session, "/Volumes/OldName/docs")
    source.volume_id = "volname:OldName"
    source.volume_rel_path = "docs"
    await session.commit()

    monkeypatch.setattr(
        watcher_module, "find_rebound_path", lambda vid, rel: str(tmp_path)
    )
    calls: list[tuple[int, str]] = []

    async def fake_sync(source_id: int, trigger: str) -> None:
        calls.append((source_id, trigger))

    watcher = WatcherService(_factory(engine), _settings(), sync_runner=fake_sync)
    watcher.start_background_loops()
    try:
        watcher.mark_dirty(source.id, immediate=True)
        assert await _wait_for(lambda: len(calls) >= 1)
    finally:
        await watcher.stop()

    await session.refresh(source)
    assert source.path == str(tmp_path)


async def test_real_observer_event_triggers_watch_sync(session, engine, tmp_path):
    """End-to-end: a real file creation reaches the sync runner.

    Uses watchdog's polling observer (deterministic in CI); the native
    observers share the same handler -> mark_dirty -> debounce path.
    """
    source = await _make_watched_source(session, str(tmp_path))
    calls: list[tuple[int, str]] = []

    async def fake_sync(source_id: int, trigger: str) -> None:
        calls.append((source_id, trigger))

    watcher = WatcherService(
        _factory(engine), _settings(), sync_runner=fake_sync, use_polling_observer=True
    )
    await watcher.start()
    try:
        # Start includes the launch reconcile; wait for it, then reset.
        assert await _wait_for(lambda: (source.id, "launch") in calls)
        calls.clear()

        (tmp_path / "new-doc.txt").write_text("hello watcher")
        assert await _wait_for(
            lambda: (source.id, "watch") in calls, timeout=10.0
        ), f"watch sync never fired; calls={calls}"
    finally:
        await watcher.stop()


def test_find_rebound_path_unique_vs_ambiguous(tmp_path, monkeypatch):
    """Rebinding only happens on an unambiguous candidate mount."""
    mount_a = tmp_path / "mountA"
    mount_b = tmp_path / "mountB"
    (mount_a / "docs").mkdir(parents=True)
    (mount_b / "docs").mkdir(parents=True)

    monkeypatch.setattr(
        volumes, "candidate_mounts", lambda vid: [str(mount_a), str(mount_b)]
    )
    assert volumes.find_rebound_path("dev:1", "docs") is None  # ambiguous

    monkeypatch.setattr(volumes, "candidate_mounts", lambda vid: [str(mount_a)])
    assert volumes.find_rebound_path("dev:1", "docs") == str(mount_a / "docs")
    assert volumes.find_rebound_path("dev:1", "nope") is None
    assert volumes.find_rebound_path("dev:1", None) is None
