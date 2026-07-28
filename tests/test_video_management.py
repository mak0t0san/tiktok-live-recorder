import json
import os
import subprocess
from types import SimpleNamespace

import ffmpeg

from utils.video_management import VideoManagement


class FakeFfmpegChain:
    def __init__(self, calls):
        self.calls = calls

    def output(self, output_file, **kwargs):
        self.calls["output_file"] = output_file
        self.calls["output_kwargs"] = kwargs
        return self

    def run(self, **kwargs):
        self.calls["ran"] = True


def _patch_ffmpeg(monkeypatch, calls, error=False):
    def fake_input(file):
        calls["input_file"] = file
        if error:
            raise ffmpeg.Error("ffmpeg", b"", b"boom")
        return FakeFfmpegChain(calls)

    monkeypatch.setattr("utils.video_management.ffmpeg.input", fake_input)


def test_converts_flv_suffix_to_mp4(tmp_path, monkeypatch):
    file = tmp_path / "TK_user_2026.07.17_flv.mp4"
    file.write_bytes(b"data")
    calls = {}
    _patch_ffmpeg(monkeypatch, calls)

    result = VideoManagement.convert_flv_to_mp4(str(file))

    assert result == str(tmp_path / "TK_user_2026.07.17.mp4")
    assert calls["output_file"] == result
    assert not file.exists()


def test_directory_named_like_flv_suffix_is_untouched(tmp_path, monkeypatch):
    directory = tmp_path / "backup_flv.mp4"
    directory.mkdir()
    file = directory / "TK_user_flv.mp4"
    file.write_bytes(b"data")
    calls = {}
    _patch_ffmpeg(monkeypatch, calls)

    result = VideoManagement.convert_flv_to_mp4(str(file))

    assert result == str(directory / "TK_user.mp4")


def test_input_without_flv_suffix_never_overwrites_itself(tmp_path, monkeypatch):
    file = tmp_path / "recording.mp4"
    file.write_bytes(b"data")
    calls = {}
    _patch_ffmpeg(monkeypatch, calls)

    result = VideoManagement.convert_flv_to_mp4(str(file))

    assert result == str(tmp_path / "recording_converted.mp4")
    assert calls["output_file"] != str(file)


def test_failed_conversion_keeps_input_and_returns_none(tmp_path, monkeypatch):
    file = tmp_path / "TK_user_flv.mp4"
    file.write_bytes(b"data")
    calls = {}
    _patch_ffmpeg(monkeypatch, calls, error=True)

    result = VideoManagement.convert_flv_to_mp4(str(file))

    assert result is None
    assert file.exists()


def test_relative_path_conversion(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    file = "TK_user_flv.mp4"
    with open(file, "wb") as f:
        f.write(b"data")
    calls = {}
    _patch_ffmpeg(monkeypatch, calls)

    result = VideoManagement.convert_flv_to_mp4(file)

    assert result == "TK_user.mp4"
    assert not os.path.exists(file)


def test_default_conversion_uses_stream_copy(tmp_path, monkeypatch):
    file = tmp_path / "TK_user_flv.mp4"
    file.write_bytes(b"data")
    calls = {}
    _patch_ffmpeg(monkeypatch, calls)

    VideoManagement.convert_flv_to_mp4(str(file))

    kwargs = calls["output_kwargs"]
    assert kwargs.get("c") == "copy"
    assert "vf" not in kwargs
    assert "c:v" not in kwargs


def test_scale_reencodes_to_max_dimensions(tmp_path, monkeypatch):
    file = tmp_path / "TK_user_flv.mp4"
    file.write_bytes(b"data")
    calls = {}
    _patch_ffmpeg(monkeypatch, calls)
    monkeypatch.setattr(
        VideoManagement,
        "_probe_max_dimensions",
        staticmethod(lambda *a, **k: (720, 1280)),
    )

    VideoManagement.convert_flv_to_mp4(str(file), scale=True)

    kwargs = calls["output_kwargs"]
    assert kwargs["c:v"] == "libx264"
    assert kwargs["c:a"] == "copy"
    assert "c" not in kwargs
    assert "720:1280" in kwargs["vf"]
    assert "scale=" in kwargs["vf"] and "pad=" in kwargs["vf"]
    assert kwargs["pix_fmt"] == "yuv420p"


def test_scale_with_bitrate_sets_both(tmp_path, monkeypatch):
    file = tmp_path / "TK_user_flv.mp4"
    file.write_bytes(b"data")
    calls = {}
    _patch_ffmpeg(monkeypatch, calls)
    monkeypatch.setattr(
        VideoManagement,
        "_probe_max_dimensions",
        staticmethod(lambda *a, **k: (540, 960)),
    )

    VideoManagement.convert_flv_to_mp4(str(file), bitrate="1M", scale=True)

    kwargs = calls["output_kwargs"]
    assert kwargs["b:v"] == "1M"
    assert kwargs["c:v"] == "libx264"
    assert "540:960" in kwargs["vf"]


def test_scale_falls_back_to_initial_probe_when_keyframe_scan_empty(
    tmp_path, monkeypatch
):
    file = tmp_path / "TK_user_flv.mp4"
    file.write_bytes(b"data")
    calls = {}
    _patch_ffmpeg(monkeypatch, calls)
    monkeypatch.setattr(
        VideoManagement, "_probe_max_dimensions", staticmethod(lambda *a, **k: None)
    )
    monkeypatch.setattr(
        VideoManagement, "_probe_dimensions", staticmethod(lambda *a, **k: (480, 854))
    )

    VideoManagement.convert_flv_to_mp4(str(file), scale=True)

    assert "480:854" in calls["output_kwargs"]["vf"]


def test_scale_falls_back_to_copy_when_probe_fails(tmp_path, monkeypatch):
    file = tmp_path / "TK_user_flv.mp4"
    file.write_bytes(b"data")
    calls = {}
    _patch_ffmpeg(monkeypatch, calls)
    monkeypatch.setattr(
        VideoManagement, "_probe_max_dimensions", staticmethod(lambda *a, **k: None)
    )
    monkeypatch.setattr(
        VideoManagement, "_probe_dimensions", staticmethod(lambda *a, **k: None)
    )

    result = VideoManagement.convert_flv_to_mp4(str(file), scale=True)

    assert result == str(tmp_path / "TK_user.mp4")
    kwargs = calls["output_kwargs"]
    assert kwargs.get("c") == "copy"
    assert "vf" not in kwargs


def test_probe_max_dimensions_picks_largest_frame(monkeypatch):
    frames = {
        "frames": [
            {"width": 540, "height": 960},
            {"width": 720, "height": 1280},
            {"width": 640, "height": 1138},
        ]
    }

    def fake_run(cmd, **kwargs):
        assert "-skip_frame" in cmd and "nokey" in cmd
        return SimpleNamespace(stdout=json.dumps(frames), stderr="", returncode=0)

    monkeypatch.setattr("utils.video_management.subprocess.run", fake_run)

    assert VideoManagement._probe_max_dimensions("x.flv") == (720, 1280)


def test_probe_max_dimensions_returns_none_on_ffprobe_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="boom")

    monkeypatch.setattr("utils.video_management.subprocess.run", fake_run)

    assert VideoManagement._probe_max_dimensions("x.flv") is None
