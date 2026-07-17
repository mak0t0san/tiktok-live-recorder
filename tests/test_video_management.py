import os

import ffmpeg

from utils.video_management import VideoManagement


class FakeFfmpegChain:
    def __init__(self, calls):
        self.calls = calls

    def output(self, output_file, **kwargs):
        self.calls["output_file"] = output_file
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
