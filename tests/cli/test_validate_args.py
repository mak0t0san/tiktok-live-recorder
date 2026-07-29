import sys

import pytest

from utils.args_handler import validate_and_parse_args
from utils.custom_exceptions import ArgsParseError
from utils.enums import Mode


def test_manual_mode_valid_with_url(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tiktok-live-recorder",
            "-mode",
            "manual",
            "-url",
            "https://www.tiktok.com/@test",
        ],
    )
    args, mode = validate_and_parse_args()
    assert args.url == "https://www.tiktok.com/@test"
    assert mode == Mode.MANUAL


def test_automatic_mode_valid_with_room_id(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["tiktok-live-recorder", "-mode", "automatic", "-room_id", "12345"],
    )
    args, mode = validate_and_parse_args()
    assert args.room_id == "12345"
    assert mode == Mode.AUTOMATIC


def test_missing_target_without_web(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tiktok-live-recorder", "-mode", "manual"])
    with pytest.raises(ArgsParseError, match="Missing target"):
        validate_and_parse_args()


def test_unknown_mode(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["tiktok-live-recorder", "-mode", "x", "-room_id", "123"],
    )
    with pytest.raises(
        ArgsParseError,
        match="Incorrect mode value. Choose between 'manual' or 'automatic'.",
    ):
        validate_and_parse_args()


def test_input_room_id_and_url(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tiktok-live-recorder",
            "-room_id",
            "12345",
            "-url",
            "https://www.tiktok.com/@test",
        ],
    )
    with pytest.raises(
        ArgsParseError,
        match="Please provide only one among room ID or URL.",
    ):
        validate_and_parse_args()


def test_valid_url(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tiktok-live-recorder",
            "-url",
            "https://www.tiktok.com/@test",
        ],
    )
    args, mode = validate_and_parse_args()
    assert args.url == "https://www.tiktok.com/@test"
    assert mode == Mode.MANUAL


def test_invalid_url(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tiktok-live-recorder",
            "-url",
            "https://www.invalid-url.com/@test",
        ],
    )
    with pytest.raises(
        ArgsParseError,
        match="The provided URL does not appear to be a valid TikTok live URL.",
    ):
        validate_and_parse_args()


def test_duration_zero_is_rejected(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["tiktok-live-recorder", "-room_id", "123", "-duration", "0"],
    )
    with pytest.raises(ArgsParseError, match="Incorrect duration value"):
        validate_and_parse_args()


def test_duration_negative_is_rejected(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["tiktok-live-recorder", "-room_id", "123", "-duration", "-5"],
    )
    with pytest.raises(ArgsParseError, match="Incorrect duration value"):
        validate_and_parse_args()


def test_output_directory_is_created(monkeypatch, tmp_path):
    output_dir = tmp_path / "new" / "nested"
    monkeypatch.setattr(
        sys,
        "argv",
        ["tiktok-live-recorder", "-room_id", "123", "-output", str(output_dir)],
    )
    args, _ = validate_and_parse_args()
    assert output_dir.is_dir()


def test_automatic_interval_less_than_one(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tiktok-live-recorder",
            "-mode",
            "automatic",
            "-room_id",
            "123",
            "-automatic_interval",
            "0",
        ],
    )
    with pytest.raises(
        ArgsParseError,
        match="Incorrect automatic_interval value. Must be one minute or more.",
    ):
        validate_and_parse_args()


def test_web_forces_automatic_mode(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["tiktok-live-recorder", "-web"],
    )
    args, mode = validate_and_parse_args()
    assert mode == Mode.AUTOMATIC
    assert args.web_host == "0.0.0.0"
    assert args.web_port == 8000


def test_web_rejects_url(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["tiktok-live-recorder", "-web", "-url", "https://www.tiktok.com/@test"],
    )
    with pytest.raises(ArgsParseError, match="-web cannot be combined"):
        validate_and_parse_args()


def test_web_rejects_unsupported_mode(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["tiktok-live-recorder", "-web", "-mode", "x"],
    )
    with pytest.raises(ArgsParseError, match="-web only supports automatic mode"):
        validate_and_parse_args()
