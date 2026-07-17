import sys

import pytest


from utils.args_handler import validate_and_parse_args
from utils.custom_exceptions import ArgsParseError
from utils.enums import Mode


def test_manual_mode_valid_with_user(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["tiktok-live-recorder", "-mode", "manual", "-user", "test"]
    )
    args, mode = validate_and_parse_args()
    assert args.user == "test"
    assert mode == Mode.MANUAL


def test_automatic_mode_valid_with_user(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["tiktok-live-recorder", "-mode", "automatic", "-user", "test"]
    )
    args, mode = validate_and_parse_args()
    assert args.user == "test"
    assert mode == Mode.AUTOMATIC


def test_followers_mode_valid_with_user(monkeypatch):
    # User input is not required for followers mode
    monkeypatch.setattr(
        sys, "argv", ["tiktok-live-recorder", "-mode", "followers", "-user", "test"]
    )
    args, mode = validate_and_parse_args()
    assert args.user == "test"
    assert mode == Mode.FOLLOWERS


def test_manual_mode_valid_without_user(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tiktok-live-recorder", "-mode", "manual"])
    with pytest.raises(
        ArgsParseError,
        match="Missing URL, username, or room ID. Please provide one of these parameters.",
    ):
        validate_and_parse_args()  # Should not raise an exception


def test_automatic_mode_valid_without_user(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tiktok-live-recorder", "-mode", "automatic"])
    with pytest.raises(
        ArgsParseError,
        match="Missing URL, username, or room ID. Please provide one of these parameters.",
    ):
        validate_and_parse_args()


def test_followers_mode_valid_without_user(monkeypatch):
    # User input is not required for followers mode
    monkeypatch.setattr(sys, "argv", ["tiktok-live-recorder", "-mode", "followers"])
    _, mode = validate_and_parse_args()  # Should not raise an exception
    assert mode == Mode.FOLLOWERS


def test_unknown_mode(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["tiktok-live-recorder", "-mode", "x", "-user", "test"]
    )
    with pytest.raises(
        ArgsParseError,
        match="Incorrect mode value. Choose between 'manual', 'automatic' or 'followers'.",
    ):
        validate_and_parse_args()  # Should raise an ArgsParseError for unknown mode


def test_input_single_user(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tiktok-live-recorder", "-user", "test"])
    args, mode = validate_and_parse_args()
    assert args.user == "test"
    assert mode == Mode.MANUAL


def test_input_single_user_with_at_sign(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tiktok-live-recorder", "-user", "@test"])
    args, mode = validate_and_parse_args()
    assert args.user == "test"
    assert mode == Mode.MANUAL


def test_input_multiple_users(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["tiktok-live-recorder", "-user", "test,test1,test2"]
    )
    args, mode = validate_and_parse_args()
    assert args.user == ["test", "test1", "test2"]
    assert mode == Mode.MANUAL


def test_input_multiple_users_with_at_sign(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["tiktok-live-recorder", "-user", "@test,@test1,@test2"]
    )
    args, mode = validate_and_parse_args()
    assert args.user == ["test", "test1", "test2"]
    assert mode == Mode.MANUAL


def test_input_user_and_room_id(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["tiktok-live-recorder", "-user", "test", "-room_id", "12345"]
    )
    with pytest.raises(
        ArgsParseError,
        match="Please provide only one among username, room ID, or URL.",
    ):
        validate_and_parse_args()


def test_input_user_and_url(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tiktok-live-recorder",
            "-user",
            "test",
            "-url",
            "https://www.tiktok.com/@test",
        ],
    )
    with pytest.raises(
        ArgsParseError,
        match="Please provide only one among username, room ID, or URL.",
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
        match="Please provide only one among username, room ID, or URL.",
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


def test_users_file_valid_with_automatic_mode(monkeypatch, tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("test\n")
    monkeypatch.setattr(
        sys,
        "argv",
        ["tiktok-live-recorder", "-mode", "automatic", "-users-file", str(users_file)],
    )
    args, mode = validate_and_parse_args()
    assert args.users_file == str(users_file)
    assert mode == Mode.AUTOMATIC


def test_users_file_requires_automatic_mode(monkeypatch, tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("test\n")
    monkeypatch.setattr(
        sys,
        "argv",
        ["tiktok-live-recorder", "-mode", "manual", "-users-file", str(users_file)],
    )
    with pytest.raises(
        ArgsParseError,
        match="-users-file is only supported with -mode automatic.",
    ):
        validate_and_parse_args()


def test_users_file_cannot_be_combined_with_user(monkeypatch, tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("test\n")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tiktok-live-recorder",
            "-mode",
            "automatic",
            "-user",
            "test",
            "-users-file",
            str(users_file),
        ],
    )
    with pytest.raises(
        ArgsParseError,
        match="-users-file cannot be combined with -user, -room_id, or -url.",
    ):
        validate_and_parse_args()


def test_users_file_must_exist(monkeypatch, tmp_path):
    missing_file = tmp_path / "does-not-exist.txt"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tiktok-live-recorder",
            "-mode",
            "automatic",
            "-users-file",
            str(missing_file),
        ],
    )
    with pytest.raises(ArgsParseError, match="Users file not found"):
        validate_and_parse_args()


def test_duration_zero_is_rejected(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["tiktok-live-recorder", "-user", "test", "-duration", "0"],
    )
    with pytest.raises(ArgsParseError, match="Incorrect duration value"):
        validate_and_parse_args()


def test_duration_negative_is_rejected(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["tiktok-live-recorder", "-user", "test", "-duration", "-5"],
    )
    with pytest.raises(ArgsParseError, match="Incorrect duration value"):
        validate_and_parse_args()


def test_output_directory_is_created(monkeypatch, tmp_path):
    output_dir = tmp_path / "new" / "nested"
    monkeypatch.setattr(
        sys,
        "argv",
        ["tiktok-live-recorder", "-user", "test", "-output", str(output_dir)],
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
            "-user",
            "test",
            "-automatic_interval",
            "0",
        ],
    )
    with pytest.raises(
        ArgsParseError,
        match="Incorrect automatic_interval value. Must be one minute or more.",
    ):
        validate_and_parse_args()
