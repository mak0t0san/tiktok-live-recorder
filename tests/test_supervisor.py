from types import SimpleNamespace

from core.supervisor import Supervisor, build_config
from utils.enums import Mode
from utils.status_store import StatusStore


def make_args(**overrides):
    args = SimpleNamespace(
        url=None,
        user=None,
        room_id=None,
        automatic_interval=5,
        proxy=None,
        output="out",
        duration=None,
        telegram=False,
        bitrate=None,
        scale=False,
        ffmpeg_path=None,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_build_config_defaults_scale_to_args():
    config = build_config(make_args(scale=True), Mode.AUTOMATIC, cookies={})
    assert config.scale is True


def test_build_config_scale_override_wins():
    # explicit scale overrides the args default in both directions
    args = make_args(scale=True)
    assert build_config(args, Mode.AUTOMATIC, cookies={}, scale=False).scale is False
    assert (
        build_config(
            make_args(scale=False), Mode.AUTOMATIC, cookies={}, scale=True
        ).scale
        is True
    )


def test_supervisor_seeds_scale_from_args():
    sup = Supervisor(make_args(scale=True), Mode.AUTOMATIC, cookies={})
    assert sup.scale is True


def test_supervisor_seeds_scale_from_db(tmp_path):
    db = tmp_path / "status.sqlite3"
    store = StatusStore(db)
    store.set_scale(True)
    store.close()

    # DB value overrides the (False) CLI default on startup
    sup = Supervisor(make_args(scale=False), Mode.AUTOMATIC, cookies={}, status_db=db)
    assert sup.scale is True


def test_supervisor_set_scale_updates_and_persists(tmp_path):
    db = tmp_path / "status.sqlite3"
    sup = Supervisor(make_args(scale=False), Mode.AUTOMATIC, cookies={}, status_db=db)

    sup.set_scale(True)
    assert sup.scale is True

    # persisted for the next process/restart
    store = StatusStore(db)
    try:
        assert store.scale_enabled() is True
    finally:
        store.close()

    sup.set_scale(False)
    assert sup.scale is False
    store = StatusStore(db)
    try:
        assert store.scale_enabled(default=True) is False
    finally:
        store.close()


def test_set_scale_without_db_is_in_memory_only():
    sup = Supervisor(make_args(scale=False), Mode.AUTOMATIC, cookies={})
    sup.set_scale(True)
    assert sup.scale is True  # no status_db: just the live value, no crash
