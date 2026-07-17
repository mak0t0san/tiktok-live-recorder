from dataclasses import dataclass

from utils.enums import Mode


@dataclass
class RecorderConfig:
    mode: Mode
    url: str | None = None
    user: str | None = None
    room_id: str | None = None
    automatic_interval: int = 5
    cookies: dict | None = None
    proxy: str | None = None
    output: str | None = None
    duration: int | None = None
    use_telegram: bool = False
    bitrate: str | None = None
    ffmpeg_path: str | None = None
    # Optional multiprocessing.Event set by the parent (supervisor/web UI) to
    # request a cooperative stop; the recorder finalizes the in-flight
    # recording and exits instead of being killed mid-file.
    stop_event: object | None = None
    # Optional path to the SQLite status database; when set, the recorder
    # reports its state there for the web dashboard.
    status_db: str | None = None
