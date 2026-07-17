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
    Loads the config file and returns it.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "..", "cookies.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_telegram_config():
    """
    Loads the telegram config file and returns it.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "..", "telegram.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_users_file(path):
    """
    Reads TikTok usernames from a text file, one per line.
    Blank lines and '#' comments (whole-line or trailing) are ignored.
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    users = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        users.append(line.removeprefix("@"))
    return users


def add_user_to_file(path, user) -> bool:
    """
    Append ``user`` to the users file. Returns False (without writing) when
    the username is already listed. Usernames are case-insensitive.
    """
    user = user.strip().removeprefix("@")
    if not user or any(c.isspace() for c in user) or "#" in user:
        raise ValueError(f"Invalid username: {user!r}")

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
