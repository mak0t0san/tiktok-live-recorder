from utils.utils import read_users_file


def test_read_users_file_returns_usernames_one_per_line(tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("alice\nbob\ncarol\n")

    assert read_users_file(str(users_file)) == ["alice", "bob", "carol"]


def test_read_users_file_skips_blank_lines_and_comments(tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("alice\n\n# this is a comment\n   \nbob\n#another comment\n")

    assert read_users_file(str(users_file)) == ["alice", "bob"]


def test_read_users_file_strips_leading_at_and_whitespace(tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("  @alice  \n@bob\ncarol\n")

    assert read_users_file(str(users_file)) == ["alice", "bob", "carol"]


def test_read_users_file_empty_file_returns_empty_list(tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("")

    assert read_users_file(str(users_file)) == []


def test_read_users_file_strips_inline_comments(tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("alice  # main account\nbob# other\n")

    assert read_users_file(str(users_file)) == ["alice", "bob"]


def test_read_users_file_strips_only_one_leading_at(tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("@@alice\n")

    assert read_users_file(str(users_file)) == ["@alice"]


def test_read_users_file_reads_utf8_usernames(tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("ütube.zoë\n", encoding="utf-8")

    assert read_users_file(str(users_file)) == ["ütube.zoë"]
