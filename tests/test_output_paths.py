from utils.output_paths import user_dir_name, user_output_dir


def test_dir_name_is_casefolded():
    # TikTok usernames are case-insensitive, so one creator must map to one
    # folder no matter how the username was capitalised in users.txt.
    assert user_dir_name("Vuilu695") == user_dir_name("vuilu695") == "vuilu695"


def test_dir_name_keeps_characters_valid_in_tiktok_usernames():
    assert user_dir_name("some.creator_99-x") == "some.creator_99-x"


def test_dir_name_strips_path_separators():
    assert "/" not in user_dir_name("a/b")
    assert "\\" not in user_dir_name("a\\b")


def test_dir_name_neutralises_parent_traversal():
    assert user_dir_name("..") == "_unknown"
    assert user_dir_name("../../etc") not in ("../../etc", "..")


def test_dir_name_does_not_produce_hidden_directories():
    assert not user_dir_name(".hidden").startswith(".")


def test_dir_name_falls_back_when_nothing_usable_remains():
    assert user_dir_name("") == "_unknown"
    assert user_dir_name("///") == "_unknown"


def test_output_dir_is_created_under_the_base(tmp_path):
    directory = user_output_dir(tmp_path, "Alice")

    assert directory == tmp_path / "alice"
    assert directory.is_dir()


def test_output_dir_accepts_an_existing_directory(tmp_path):
    first = user_output_dir(tmp_path, "alice")
    second = user_output_dir(tmp_path, "alice")

    assert first == second
    assert second.is_dir()


def test_output_dir_accepts_a_string_base(tmp_path):
    directory = user_output_dir(str(tmp_path), "alice")

    assert directory == tmp_path / "alice"


def test_output_dir_creates_missing_parents(tmp_path):
    base = tmp_path / "not" / "there" / "yet"

    assert user_output_dir(base, "alice").is_dir()
