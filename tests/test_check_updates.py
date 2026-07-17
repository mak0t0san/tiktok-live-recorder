from check_updates import parse_version


def test_two_part_and_three_part_versions_compare_correctly():
    assert parse_version("7.7") < parse_version("7.7.1")


def test_equal_versions_are_not_newer():
    assert not parse_version("7.7.1") > parse_version("7.7.1")


def test_newer_local_version_is_not_an_update():
    assert not parse_version("7.7.1") > parse_version("7.8")


def test_malformed_version_never_triggers_update():
    assert not parse_version("garbage") > parse_version("7.7.1")
