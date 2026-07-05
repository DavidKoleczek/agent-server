from collections.abc import Sequence
from enum import Enum
from pathlib import Path, PurePath
import posixpath
import re

from pydantic import BaseModel


class ConstraintPolicy(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class ConstraintRule(BaseModel):
    pattern: str
    policy: ConstraintPolicy


WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:[/\\]")
REGEX_SPECIAL_CHARS = frozenset({".", "^", "$", "+", "{", "}", "[", "]", "|", "(", ")"})


def check_path_constraint(
    file_path: Path | PurePath,
    rules: Sequence[ConstraintRule],
    working_dir: Path | PurePath,
    default_policy: ConstraintPolicy,
    home_dir: Path | PurePath | None = None,
) -> ConstraintPolicy:
    """Check if a path matches any constraint rules.

    Rules are evaluated in order; first match wins.

    Pattern syntax:
        - `//path` - Absolute path from filesystem root.
        - `C:/path` or `C:\\path` - Absolute Windows drive path.
        - `~/path` - Path from home directory.
        - `/path` or `./path` - Path from working_dir root.
        - `path` - Path under working_dir, with bare filenames matching at any depth.

    Paths are normalized to POSIX form before matching.

    On Windows, drive roots are represented as POSIX drive roots, so `C:\\Users\\alice` and `C:/Users/alice`
    become `/c/Users/alice`.

    Args:
        file_path: Target path to check against the constraint rules.
        rules: Ordered constraint rules to evaluate.
        working_dir: Base directory for relative and working-directory-rooted rules.
        default_policy: Policy returned when no rule matches.
        home_dir: Home directory used for `~/` rules. Defaults to `Path.home()`.
    """
    resolved_working_dir = _normalize_absolute_path(working_dir)
    resolved_home_dir = _normalize_absolute_path(home_dir or Path.home())
    resolved_path = _normalize_target_path(file_path, working_dir, home_dir or Path.home())

    for rule in rules:
        anchor, pattern, rooted = _parse_rule_pattern(rule.pattern, resolved_working_dir, resolved_home_dir)
        relative_path = _relative_to_anchor(resolved_path, anchor)
        if relative_path is not None and _matches_gitignore_pattern(relative_path, pattern, rooted=rooted):
            return rule.policy

    return default_policy


def _normalize_target_path(file_path: Path | PurePath, working_dir: Path | PurePath, home_dir: Path | PurePath) -> str:
    path_text = str(file_path)

    if _is_home_relative_path(path_text):
        return _normalize_home_relative_path(path_text, home_dir)

    if _is_absolute_path(file_path):
        return _normalize_absolute_path(file_path)

    if isinstance(working_dir, Path):
        return _normalize_absolute_path(working_dir / path_text)

    return _normalize_path_string(f"{working_dir}/{path_text}")


def _normalize_home_relative_path(path_text: str, home_dir: Path | PurePath) -> str:
    suffix = path_text[1:].replace("\\", "/")
    return _normalize_path_string(f"{_normalize_absolute_path(home_dir)}{suffix}")


def _normalize_absolute_path(path: Path | PurePath) -> str:
    path_text = str(path)

    if _is_home_relative_path(path_text):
        return _normalize_home_relative_path(path_text, Path.home())

    if WINDOWS_ABSOLUTE_PATH_RE.match(path_text):
        return _normalize_path_string(path_text)

    if isinstance(path, Path):
        return _normalize_path_string(str(path.resolve()))

    return _normalize_path_string(path_text)


def _normalize_path_string(path: str) -> str:
    normalized_path = path.replace("\\", "/")
    drive_match = WINDOWS_ABSOLUTE_PATH_RE.match(normalized_path)

    if drive_match:
        drive = normalized_path[0].lower()
        normalized_path = f"/{drive}{normalized_path[2:]}"

    if normalized_path.startswith("//"):
        normalized_path = f"/{normalized_path.lstrip('/')}"

    normalized_path = posixpath.normpath(normalized_path)
    if normalized_path == ".":
        return ""

    return normalized_path


def _is_home_relative_path(path_text: str) -> bool:
    return path_text == "~" or path_text.startswith(("~/", "~\\"))


def _is_absolute_path(path: Path | PurePath) -> bool:
    path_text = str(path)
    return WINDOWS_ABSOLUTE_PATH_RE.match(path_text) is not None or path.is_absolute()


def _parse_rule_pattern(pattern: str, working_dir: str, home_dir: str) -> tuple[str, str, bool]:
    normalized_pattern = pattern.replace("\\", "/")

    if WINDOWS_ABSOLUTE_PATH_RE.match(normalized_pattern):
        return "/", _normalize_windows_rule_pattern(normalized_pattern).lstrip("/"), True

    if normalized_pattern.startswith("//"):
        return "/", normalized_pattern[2:].lstrip("/"), True

    if normalized_pattern == "~":
        return home_dir, "", True

    if normalized_pattern.startswith("~/"):
        return home_dir, normalized_pattern[2:], True

    if normalized_pattern.startswith("/"):
        return working_dir, normalized_pattern[1:], True

    if normalized_pattern.startswith("./"):
        return working_dir, normalized_pattern[2:], True

    return working_dir, normalized_pattern, False


def _normalize_windows_rule_pattern(pattern: str) -> str:
    normalized_pattern = _normalize_path_string(pattern)
    if pattern.endswith("/") and not normalized_pattern.endswith("/"):
        return f"{normalized_pattern}/"

    return normalized_pattern


def _relative_to_anchor(path: str, anchor: str) -> str | None:
    if anchor in {"", "/"}:
        return path.lstrip("/")

    if path == anchor:
        return ""

    anchor_prefix = f"{anchor.rstrip('/')}/"
    if path.startswith(anchor_prefix):
        return path.removeprefix(anchor_prefix)

    return None


def _matches_gitignore_pattern(path: str, pattern: str, *, rooted: bool) -> bool:
    directory_pattern = pattern.endswith("/")
    normalized_path = path.strip("/")
    normalized_pattern = pattern.strip("/")

    if normalized_pattern == "":
        return normalized_path == ""

    if directory_pattern:
        return normalized_path == normalized_pattern or normalized_path.startswith(f"{normalized_pattern}/")

    if "/" not in normalized_pattern and not rooted:
        return any(_matches_path_pattern(segment, normalized_pattern) for segment in normalized_path.split("/"))

    return _matches_path_pattern(normalized_path, normalized_pattern)


def _matches_path_pattern(path: str, pattern: str) -> bool:
    return re.fullmatch(_translate_pattern(pattern), path) is not None


def _translate_pattern(pattern: str) -> str:
    regex = []
    index = 0

    while index < len(pattern):
        char = pattern[index]

        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                is_segment = (index == 0 or pattern[index - 1] == "/") and (
                    index + 2 == len(pattern) or pattern[index + 2] == "/"
                )
                if is_segment and index + 2 < len(pattern) and pattern[index + 2] == "/":
                    regex.append("(?:[^/]+/)*")
                    index += 3
                else:
                    regex.append(".*")
                    index += 2
            else:
                regex.append("[^/]*")
                index += 1
        elif char == "?":
            regex.append("[^/]")
            index += 1
        else:
            if char in REGEX_SPECIAL_CHARS:
                regex.append(f"\\{char}")
            else:
                regex.append(char)
            index += 1

    return "".join(regex)
