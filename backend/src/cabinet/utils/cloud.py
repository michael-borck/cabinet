"""Cloud-placeholder detection (Phase 1b, design §4.2 and §7 D4).

A *cloud placeholder* (iCloud Drive, OneDrive Files On-Demand, ...) is a
file that looks present -- name, size, mtime -- but whose bytes are not on
the local disk. Reading it forces a download, so the scanner and pipeline
must not hash or extract one implicitly.

Detection is a metadata-only ``stat`` (it never opens the file), which is
why it is safe to run while reconciling. Each platform exposes the state
differently:

* macOS/APFS marks the file ``SF_DATALESS`` in ``st_flags``.
* Windows sets ``FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS``,
  ``FILE_ATTRIBUTE_RECALL_ON_OPEN`` or ``FILE_ATTRIBUTE_OFFLINE`` in
  ``st_file_attributes``.
* Other platforms (Linux/BSD) have no portable marker; detection reports
  ``False`` rather than guessing, so a FUSE/cloud mount there behaves as a
  normal local file. A generic "zero blocks but non-zero size" heuristic
  is deliberately not used: it false-positives on sparse files and some
  network filesystems.
"""

from __future__ import annotations

import os
import sys

# <sys/stat.h> on Darwin: file is a dataless object.
SF_DATALESS = 0x40000000

# <winnt.h>: file is offline (dehydrated) or its data must be recalled.
FILE_ATTRIBUTE_OFFLINE = 0x00001000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000

_WINDOWS_PLACEHOLDER_ATTRS = (
    FILE_ATTRIBUTE_OFFLINE
    | FILE_ATTRIBUTE_RECALL_ON_OPEN
    | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)


def is_cloud_placeholder(
    path: str,
    *,
    st: os.stat_result | None = None,
    platform: str | None = None,
) -> bool:
    """Return True when *path* is a dataless cloud placeholder.

    ``st`` lets a caller that already stat'ed the file avoid a second
    syscall. ``platform`` is injectable for tests; it defaults to the
    running interpreter's platform. Failures (missing file, permission
    errors) report ``False`` -- availability is best-effort.
    """
    platform = platform or sys.platform
    if st is None:
        try:
            st = os.stat(path)
        except OSError:
            return False

    if platform == "darwin":
        return bool(getattr(st, "st_flags", 0) & SF_DATALESS)
    if platform == "win32":
        return bool(getattr(st, "st_file_attributes", 0) & _WINDOWS_PLACEHOLDER_ATTRS)
    return False
