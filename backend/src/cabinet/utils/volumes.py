"""Removable-volume identity helpers (Phase 1b, design §4.4).

A source records *which* volume its root lives on and the root's path
relative to that volume, so a drive that remounts somewhere else (new
drive letter, ``/Volumes/Name-1``) can be found again instead of being
treated as gone. Everything here is best-effort and read-only: no match
simply means the source stays "unavailable".
"""

from __future__ import annotations

import glob
import os
import sys


def mount_point(path: str) -> str:
    """Return the mount point (volume root) containing *path*."""
    current = os.path.abspath(path)
    while not os.path.ismount(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def describe_source_root(path: str) -> tuple[str | None, str | None]:
    """Return ``(volume_id, volume_rel_path)`` for a source root.

    ``volume_id`` prefers a human-stable name (macOS ``/Volumes/<name>``,
    Windows anchor) and falls back to the mount's device number.
    ``volume_rel_path`` uses forward slashes; empty means the volume root
    itself is the source.
    """
    try:
        mount = mount_point(path)
        rel = os.path.relpath(os.path.abspath(path), mount)
        rel = "" if rel == "." else rel.replace(os.sep, "/")
        if mount.startswith("/Volumes/"):
            return f"volname:{mount.split('/')[2]}", rel
        if sys.platform == "win32":
            return f"anchor:{mount}", rel
        return f"dev:{os.stat(mount).st_dev}", rel
    except OSError:
        return None, None


def candidate_mounts(volume_id: str | None) -> list[str]:
    """Mount points worth trying when a recorded path has vanished."""
    candidates: list[str] = []
    if volume_id and volume_id.startswith("volname:"):
        candidates.append(f"/Volumes/{volume_id.split(':', 1)[1]}")
    candidates += sorted(glob.glob("/Volumes/*"))
    candidates.append("/")
    candidates += sorted(glob.glob("/media/*") + glob.glob("/media/*/*"))
    candidates += sorted(glob.glob("/mnt/*"))
    if sys.platform == "win32":
        candidates += [f"{letter}:\\" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    seen: set[str] = set()
    unique: list[str] = []
    for cand in candidates:
        if cand not in seen and os.path.isdir(cand):
            seen.add(cand)
            unique.append(cand)
    return unique


def find_rebound_path(
    volume_id: str | None, volume_rel_path: str | None
) -> str | None:
    """Find where a vanished source root lives now, or None if unsure.

    Only an unambiguous match is returned: the same relative directory
    existing under exactly one candidate mount (preferring the mount the
    recorded volume id names when several could apply by name).
    """
    if volume_rel_path is None:
        return None
    rel_os = volume_rel_path.replace("/", os.sep) if volume_rel_path else ""
    found: list[str] = []
    for mount in candidate_mounts(volume_id):
        candidate = os.path.join(mount, rel_os) if rel_os else mount
        if os.path.isdir(candidate):
            found.append(os.path.abspath(candidate))
    if volume_id and volume_id.startswith("volname:"):
        named = f"/Volumes/{volume_id.split(':', 1)[1]}"
        named_match = [p for p in found if p.startswith(named)]
        if len(named_match) == 1:
            return named_match[0]
    return found[0] if len(found) == 1 else None
