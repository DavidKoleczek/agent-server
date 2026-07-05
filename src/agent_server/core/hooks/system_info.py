from datetime import datetime
import platform as platform_module
import sys
from zoneinfo import ZoneInfo


def platform() -> str:
    platform_map = {
        "aix": "AIX",
        "android": "android",
        "emscripten": "Emscripten",
        "freebsd": "FreeBSD",
        "ios": "iOS",
        "linux": "Linux",
        "darwin": "macOS",
        "win32": "Windows",
        "cygwin": "Windows/Cygwin",
        "wasi": "WASI",
    }
    return platform_map.get(sys.platform, sys.platform)


def os_version() -> str:
    return platform_module.platform()


def todays_date(tz: str = "UTC") -> str:
    """Returns today's date in ISO format (YYYY-MM-DD).

    Args:
        tz: IANA timezone name (e.g., "UTC", "America/New_York", "Europe/London").
    """
    return datetime.now(tz=ZoneInfo(tz)).date().isoformat()
