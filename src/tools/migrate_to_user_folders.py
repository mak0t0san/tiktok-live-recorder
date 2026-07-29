#!/usr/bin/env python3
"""
Move flat recordings into the per-user folders the recorder now writes to.

Run this once, by hand, while nothing is recording -- it is not wired into
startup on purpose, because moving a file out from under a live ffmpeg process
would corrupt that recording.

    uv run python src/tools/migrate_to_user_folders.py /path/to/output
    uv run python src/tools/migrate_to_user_folders.py /path/to/output --apply

Inside the container:

    docker exec -it <container> python /app/tools/migrate_to_user_folders.py /data

The default is a dry run; nothing moves until ``--apply`` is passed. Files whose
destination already exists are reported as conflicts and left alone, so the
script can never destroy a recording.

The status database is deliberately left untouched. Its live ``recordings``
rows are rewritten by each recorder process on the next run, and the dashboard
reads only ``ended_at``/``duration`` out of ``recording_history`` -- no consumer
follows the historical ``output_path``, so rewriting it would be churn without
a reader.
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.output_paths import user_dir_name  # noqa: E402

# Recorder filenames look like TK_<user>_2026.07.17_10-00-00[_flv].mp4. The
# username group is greedy so it keeps underscores of its own ("some_creator_99")
# and backtracks onto the trailing timestamp.
_RECORDING = re.compile(
    r"^TK_(?P<user>.+)_\d{4}\.\d{2}\.\d{2}_\d{2}-\d{2}-\d{2}(_flv)?$"
)


@dataclass
class MigrationPlan:
    moves: list[tuple[Path, Path]] = field(default_factory=list)
    conflicts: list[tuple[Path, Path]] = field(default_factory=list)
    unrecognised: list[Path] = field(default_factory=list)


def username_from_filename(name: str) -> str | None:
    """Extract the recorded username from an mp4 filename, or None."""
    match = _RECORDING.match(Path(name).stem)
    return match.group("user") if match else None


def plan_moves(output_dir) -> MigrationPlan:
    """
    Work out which root-level recordings belong in which per-user folder.

    Only the top level is inspected: anything already inside a subfolder has
    been migrated (or was written by the current recorder) and is left alone.
    """
    root = Path(output_dir)
    plan = MigrationPlan()

    for path in sorted(root.glob("*.mp4")):
        if not path.is_file():
            continue
        user = username_from_filename(path.name)
        if user is None:
            plan.unrecognised.append(path)
            continue
        destination = root / user_dir_name(user) / path.name
        if destination.exists():
            plan.conflicts.append((path, destination))
        else:
            plan.moves.append((path, destination))

    return plan


def apply_moves(moves) -> int:
    """Perform the planned moves, skipping any destination that now exists."""
    moved = 0
    for src, dst in moves:
        if dst.exists():
            print(f"  skipped (destination appeared): {src.name}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        moved += 1
    return moved


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Move flat recordings into per-user folders."
    )
    parser.add_argument("output_dir", help="the recorder's output directory")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually move the files (default: show what would happen)",
    )
    args = parser.parse_args(argv)

    root = Path(args.output_dir)
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    plan = plan_moves(root)

    for src, dst in plan.moves:
        print(f"  {src.name}  ->  {dst.relative_to(root).as_posix()}")
    for path in plan.unrecognised:
        print(f"  leaving alone (unrecognised name): {path.name}")
    for src, dst in plan.conflicts:
        print(f"  conflict: {dst.relative_to(root).as_posix()} already exists")

    if not plan.moves and not plan.conflicts:
        print("Nothing to migrate.")
        return 0

    if not args.apply:
        print(
            f"\nDry run: {len(plan.moves)} file(s) would move. "
            f"Re-run with --apply to do it."
        )
    else:
        moved = apply_moves(plan.moves)
        print(f"\nMoved {moved} file(s).")

    if plan.conflicts:
        print(
            f"{len(plan.conflicts)} conflict(s) left in place -- resolve them by hand."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
