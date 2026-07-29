"""
Per-user output directories.

Every recording is written to ``<output>/<username>/`` so one creator's videos
stay together instead of piling into a single flat folder. The username is
case-folded (TikTok usernames are case-insensitive, so ``Vuilu695`` and
``vuilu695`` are the same creator and must share one folder) and reduced to a
safe path component, which makes it structurally impossible for a malformed
``users.txt`` entry to escape the output tree.
"""

import re
from pathlib import Path

# Characters that survive into a directory name. Real TikTok usernames only use
# letters, digits, underscores and periods; the hyphen is tolerated because it
# is harmless and shows up in hand-written users.txt entries.
_UNSAFE = re.compile(r"[^a-z0-9._-]+")

# Leading dots would create hidden directories; leading/trailing dots and
# underscores are also poor directory names on Windows and in shells.
_TRIM = "._"

FALLBACK_DIR_NAME = "_unknown"


def user_dir_name(user: str) -> str:
    """
    Reduce a username to a safe, case-insensitive directory name.

    Returns :data:`FALLBACK_DIR_NAME` when nothing usable remains, so a blank
    or fully-unsafe username still records somewhere predictable instead of
    raising mid-stream.
    """
    name = _UNSAFE.sub("_", (user or "").casefold()).strip(_TRIM)
    return name or FALLBACK_DIR_NAME


def user_output_dir(base, user: str) -> Path:
    """
    Return ``base/<user>``, creating it (and any missing parents) if needed.

    Raises ``OSError`` if the directory cannot be created; callers decide
    whether that is fatal.
    """
    directory = Path(base) / user_dir_name(user)
    directory.mkdir(parents=True, exist_ok=True)
    return directory
