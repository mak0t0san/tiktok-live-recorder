from pathlib import Path

from tools.migrate_to_user_folders import apply_moves, main, plan_moves


def _touch(path: Path, data: bytes = b"video") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_plan_moves_nests_a_flat_recording_under_its_user(tmp_path):
    _touch(tmp_path / "TK_alice_2026.07.17_10-00-00.mp4")

    plan = plan_moves(tmp_path)

    assert plan.moves == [
        (
            tmp_path / "TK_alice_2026.07.17_10-00-00.mp4",
            tmp_path / "alice" / "TK_alice_2026.07.17_10-00-00.mp4",
        )
    ]
    assert plan.conflicts == []
    assert plan.unrecognised == []


def test_plan_moves_casefolds_the_destination_folder(tmp_path):
    _touch(tmp_path / "TK_Alice_2026.07.17_10-00-00.mp4")

    ((_, dst),) = plan_moves(tmp_path).moves

    # matches the folder the recorder itself would write to
    assert dst.parent == tmp_path / "alice"


def test_plan_moves_handles_usernames_containing_underscores(tmp_path):
    _touch(tmp_path / "TK_some_creator_99_2026.07.17_10-00-00_flv.mp4")

    ((_, dst),) = plan_moves(tmp_path).moves

    assert dst.parent == tmp_path / "some_creator_99"


def test_plan_moves_ignores_already_nested_recordings(tmp_path):
    _touch(tmp_path / "alice" / "TK_alice_2026.07.17_10-00-00.mp4")

    plan = plan_moves(tmp_path)

    assert plan.moves == []
    assert plan.unrecognised == []


def test_plan_moves_reports_unrecognised_filenames_without_moving_them(tmp_path):
    stray = _touch(tmp_path / "holiday-clip.mp4")

    plan = plan_moves(tmp_path)

    assert plan.moves == []
    assert plan.unrecognised == [stray]


def test_plan_moves_ignores_non_mp4_files(tmp_path):
    _touch(tmp_path / "tiktok-recorder.log", b"log")
    _touch(tmp_path / ".tiktok-recorder-status.sqlite3", b"db")

    plan = plan_moves(tmp_path)

    assert plan.moves == []
    assert plan.unrecognised == []


def test_plan_moves_reports_a_conflict_instead_of_planning_an_overwrite(tmp_path):
    src = _touch(tmp_path / "TK_alice_2026.07.17_10-00-00.mp4", b"new")
    dst = _touch(tmp_path / "alice" / "TK_alice_2026.07.17_10-00-00.mp4", b"existing")

    plan = plan_moves(tmp_path)

    assert plan.moves == []
    assert plan.conflicts == [(src, dst)]


def test_apply_moves_moves_the_files(tmp_path):
    src = _touch(tmp_path / "TK_alice_2026.07.17_10-00-00.mp4", b"video")

    moved = apply_moves(plan_moves(tmp_path).moves)

    assert moved == 1
    assert not src.exists()
    assert (tmp_path / "alice" / "TK_alice_2026.07.17_10-00-00.mp4").read_bytes() == (
        b"video"
    )


def test_apply_moves_refuses_to_overwrite_an_existing_destination(tmp_path):
    src = _touch(tmp_path / "TK_alice_2026.07.17_10-00-00.mp4", b"new")
    dst = _touch(tmp_path / "alice" / "TK_alice_2026.07.17_10-00-00.mp4", b"existing")

    # belt and braces: plan_moves already filters these out, but apply_moves
    # must never destroy a recording even if handed a stale plan
    moved = apply_moves([(src, dst)])

    assert moved == 0
    assert dst.read_bytes() == b"existing"
    assert src.read_bytes() == b"new"


def test_main_defaults_to_a_dry_run(tmp_path, capsys):
    src = _touch(tmp_path / "TK_alice_2026.07.17_10-00-00.mp4")

    exit_code = main([str(tmp_path)])

    assert exit_code == 0
    assert src.exists(), "a dry run must not touch the files"
    out = capsys.readouterr().out
    assert "alice/TK_alice_2026.07.17_10-00-00.mp4" in out
    assert "--apply" in out


def test_main_with_apply_moves_the_files(tmp_path, capsys):
    src = _touch(tmp_path / "TK_alice_2026.07.17_10-00-00.mp4")

    exit_code = main([str(tmp_path), "--apply"])

    assert exit_code == 0
    assert not src.exists()
    assert (tmp_path / "alice" / "TK_alice_2026.07.17_10-00-00.mp4").is_file()


def test_main_reports_conflicts_and_exits_non_zero(tmp_path, capsys):
    _touch(tmp_path / "TK_alice_2026.07.17_10-00-00.mp4", b"new")
    _touch(tmp_path / "alice" / "TK_alice_2026.07.17_10-00-00.mp4", b"existing")

    exit_code = main([str(tmp_path), "--apply"])

    assert exit_code == 1
    assert "conflict" in capsys.readouterr().out.lower()


def test_main_rejects_a_missing_output_dir(tmp_path, capsys):
    exit_code = main([str(tmp_path / "nope")])

    assert exit_code == 2
    assert "not a directory" in capsys.readouterr().err.lower()
