"""Tests for cloud-placeholder detection (design §4.2, §7 D4)."""

from types import SimpleNamespace

from cabinet.utils import cloud
from cabinet.utils.cloud import (
    FILE_ATTRIBUTE_OFFLINE,
    FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
    FILE_ATTRIBUTE_RECALL_ON_OPEN,
    SF_DATALESS,
    is_cloud_placeholder,
)


def _stat(**attrs) -> SimpleNamespace:
    """Stand-in for os.stat_result; the detector only reads attributes."""
    return SimpleNamespace(**attrs)


def test_macos_dataless_flag():
    assert is_cloud_placeholder("x", st=_stat(st_flags=SF_DATALESS), platform="darwin")
    assert not is_cloud_placeholder("x", st=_stat(st_flags=0), platform="darwin")


def test_windows_recall_attributes():
    for attr in (
        FILE_ATTRIBUTE_OFFLINE,
        FILE_ATTRIBUTE_RECALL_ON_OPEN,
        FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
    ):
        assert is_cloud_placeholder(
            "x", st=_stat(st_file_attributes=attr), platform="win32"
        )
    assert not is_cloud_placeholder(
        "x", st=_stat(st_file_attributes=0), platform="win32"
    )


def test_other_platforms_never_guess():
    """Linux/BSD have no marker; zero blocks must not be treated as cloud."""
    assert not is_cloud_placeholder("x", st=_stat(st_blocks=0), platform="linux")


def test_missing_attributes_mean_false():
    assert not is_cloud_placeholder("x", st=SimpleNamespace(), platform="darwin")
    assert not is_cloud_placeholder("x", st=SimpleNamespace(), platform="win32")


def test_unstatable_path_is_not_a_placeholder(monkeypatch):
    def boom(_path):
        raise OSError("gone")

    monkeypatch.setattr(cloud.os, "stat", boom)
    assert not is_cloud_placeholder("/nope", platform="darwin")
