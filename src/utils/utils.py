import json
import os

from utils.enums import Info


def banner() -> None:
    """
    Prints a banner with the name of the tool and its version number.
    """
    print(Info.BANNER, flush=True)


def read_cookies():
    """
    Loads the legacy cookies.json file.

    Prefer ``utils.cookies.resolve_cookies`` for runtime use — this remains
    for callers that still expect a raw file read.
    """
    from utils.cookies import read_legacy_cookies_file

    return read_legacy_cookies_file()


def read_telegram_config():
    """
    Loads the telegram config file and returns it.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "..", "telegram.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_username(user: str) -> str:
    """
    Strip whitespace / leading ``@`` and validate a TikTok username.
    Raises ValueError when the name is empty, contains whitespace / ``#``,
    or looks like a path component (``/``, ``\\``, ``..``).
    """
    user = user.strip().removeprefix("@")
    if (
        not user
        or any(c.isspace() for c in user)
        or "#" in user
        or "/" in user
        or "\\" in user
        or user in {".", ".."}
        or ".." in user
    ):
        raise ValueError(f"Invalid username: {user!r}")
    return user


def parse_users_text(text: str) -> list:
    """
    Parse a users-list export: one username per line, ``#`` comments ignored.
    Invalid lines are skipped; duplicates are dropped case-insensitively.
    """
    users = []
    seen = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            name = normalize_username(line)
        except ValueError:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        users.append(name)
    return users


def read_users_file(path):
    """
    Reads TikTok usernames from a text file, one per line.
    Blank lines and '#' comments (whole-line or trailing) are ignored.

    Kept for one-time migration of legacy users.txt into the status DB.
    """
    with open(path, "r", encoding="utf-8") as f:
        return parse_users_text(f.read())


def add_user_to_file(path, user) -> bool:
    """
    Append ``user`` to the users file. Returns False (without writing) when
    the username is already listed. Usernames are case-insensitive.

    Legacy helper retained for tests / migration tooling.
    """
    user = normalize_username(user)

    existing = {u.casefold() for u in read_users_file(path)}
    if user.casefold() in existing:
        return False

    with open(path, "a+", encoding="utf-8") as f:
        f.seek(0)
        content = f.read()
        if content and not content.endswith("\n"):
            f.write("\n")
        f.write(f"{user}\n")
    return True


def remove_user_from_file(path, user) -> bool:
    """
    Remove ``user``'s line from the users file, preserving comments, blank
    lines, and every other entry. Returns False when the user wasn't listed.

    Legacy helper retained for tests / migration tooling.
    """
    target = user.strip().removeprefix("@").casefold()

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    kept, removed = [], False
    for line in lines:
        name = line.split("#", 1)[0].strip().removeprefix("@")
        if name and name.casefold() == target:
            removed = True
            continue
        kept.append(line)

    if removed:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(kept)
    return removed


def is_termux() -> bool:
    """
    Checks if the script is running in Termux.

    Returns:
        bool: True if running in Termux, False otherwise.
    """
    import distro
    import platform

    return platform.system().lower() == "linux" and distro.like() == ""


def is_windows() -> bool:
    """
    Checks if the script is running on Windows.

    Returns:
        bool: True if running on Windows, False otherwise.
    """
    import platform

    return platform.system().lower() == "windows"


def is_linux() -> bool:
    """
    Checks if the script is running on Linux.

    Returns:
        bool: True if running on Linux, False otherwise.
    """
    import platform

    return platform.system().lower() == "linux"
