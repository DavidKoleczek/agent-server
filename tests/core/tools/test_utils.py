from pathlib import Path, PurePosixPath, PureWindowsPath

from agent_server.core.tools._utils import ConstraintPolicy, ConstraintRule, check_path_constraint


def test_allow_directory_and_everything_under_it(tmp_path: Path) -> None:
    rules = [ConstraintRule(pattern="src/**", policy=ConstraintPolicy.ALLOW)]

    assert (
        check_path_constraint(tmp_path / "src" / "main.py", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ALLOW
    )
    assert (
        check_path_constraint(tmp_path / "src" / "deep" / "nested.py", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ALLOW
    )
    assert (
        check_path_constraint(tmp_path / "other" / "file.txt", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ASK
    )


def test_allow_dirs_but_deny_specific_subdirs(tmp_path: Path) -> None:
    rules = [
        ConstraintRule(pattern="data/public/secrets/**", policy=ConstraintPolicy.DENY),
        ConstraintRule(pattern="data/public/**", policy=ConstraintPolicy.ALLOW),
        ConstraintRule(pattern="data/logs/**", policy=ConstraintPolicy.ALLOW),
    ]

    assert (
        check_path_constraint(tmp_path / "data" / "public" / "readme.txt", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ALLOW
    )
    assert (
        check_path_constraint(
            tmp_path / "data" / "public" / "secrets" / "keys.txt", rules, tmp_path, ConstraintPolicy.ASK
        )
        == ConstraintPolicy.DENY
    )
    assert (
        check_path_constraint(tmp_path / "data" / "logs" / "app.log", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ALLOW
    )
    assert (
        check_path_constraint(tmp_path / "data" / "private" / "file.txt", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ASK
    )
    assert (
        check_path_constraint(PurePosixPath("/home/usr/temp.txt"), rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ASK
    )


def test_deny_dotfiles_but_allow_specific_extensions(tmp_path: Path) -> None:
    rules = [
        ConstraintRule(pattern="*.pdf", policy=ConstraintPolicy.ALLOW),
        ConstraintRule(pattern="**/*.pdf", policy=ConstraintPolicy.ALLOW),
        ConstraintRule(pattern="*.docx", policy=ConstraintPolicy.ALLOW),
        ConstraintRule(pattern="**/*.docx", policy=ConstraintPolicy.ALLOW),
        ConstraintRule(pattern=".gitignore", policy=ConstraintPolicy.ALLOW),
        ConstraintRule(pattern="**/.gitignore", policy=ConstraintPolicy.ALLOW),
        ConstraintRule(pattern=".*", policy=ConstraintPolicy.DENY),
        ConstraintRule(pattern="**/.*", policy=ConstraintPolicy.DENY),
    ]

    assert (
        check_path_constraint(tmp_path / "report.pdf", rules, tmp_path, ConstraintPolicy.ASK) == ConstraintPolicy.ALLOW
    )
    assert (
        check_path_constraint(tmp_path / "docs" / "report.pdf", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ALLOW
    )
    assert check_path_constraint(tmp_path / "doc.docx", rules, tmp_path, ConstraintPolicy.ASK) == ConstraintPolicy.ALLOW
    assert (
        check_path_constraint(tmp_path / ".gitignore", rules, tmp_path, ConstraintPolicy.ASK) == ConstraintPolicy.ALLOW
    )
    assert (
        check_path_constraint(tmp_path / "config" / ".gitignore", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ALLOW
    )
    assert check_path_constraint(tmp_path / ".env", rules, tmp_path, ConstraintPolicy.ASK) == ConstraintPolicy.DENY
    assert check_path_constraint(tmp_path / "readme.txt", rules, tmp_path, ConstraintPolicy.ASK) == ConstraintPolicy.ASK


def test_default_policy_when_no_rules_match(tmp_path: Path) -> None:
    rules: list[ConstraintRule] = []

    assert (
        check_path_constraint(tmp_path / "any" / "file.txt", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ASK
    )


def test_first_matching_rule_wins(tmp_path: Path) -> None:
    rules = [
        ConstraintRule(pattern="//etc/hosts", policy=ConstraintPolicy.ALLOW),
        ConstraintRule(pattern="//etc/*", policy=ConstraintPolicy.DENY),
    ]

    assert (
        check_path_constraint(PurePosixPath("/etc/hosts"), rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ALLOW
    )
    assert (
        check_path_constraint(PurePosixPath("/etc/passwd"), rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.DENY
    )


def test_absolute_pattern_with_double_slash(tmp_path: Path) -> None:
    rules = [ConstraintRule(pattern="//usr/local/**", policy=ConstraintPolicy.ALLOW)]

    assert (
        check_path_constraint(PurePosixPath("/usr/local/bin/python"), rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ALLOW
    )
    assert (
        check_path_constraint(PurePosixPath("/usr/bin/python"), rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ASK
    )


def test_bare_filename_matches_at_any_depth_under_working_dir(tmp_path: Path) -> None:
    rules = [ConstraintRule(pattern=".env", policy=ConstraintPolicy.DENY)]

    assert check_path_constraint(tmp_path / ".env", rules, tmp_path, ConstraintPolicy.ASK) == ConstraintPolicy.DENY
    assert (
        check_path_constraint(tmp_path / "config" / ".env", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.DENY
    )
    assert (
        check_path_constraint(tmp_path.parents[0] / ".env", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ASK
    )


def test_double_star_filename_pattern_matches_root_and_nested_files(tmp_path: Path) -> None:
    rules = [ConstraintRule(pattern="**/.env", policy=ConstraintPolicy.DENY)]

    assert check_path_constraint(tmp_path / ".env", rules, tmp_path, ConstraintPolicy.ASK) == ConstraintPolicy.DENY
    assert (
        check_path_constraint(tmp_path / "config" / ".env", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.DENY
    )


def test_single_star_does_not_cross_path_segments(tmp_path: Path) -> None:
    rules = [ConstraintRule(pattern="/docs/*.pdf", policy=ConstraintPolicy.ALLOW)]

    assert (
        check_path_constraint(tmp_path / "docs" / "report.pdf", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ALLOW
    )
    assert (
        check_path_constraint(tmp_path / "docs" / "archive" / "report.pdf", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ASK
    )


def test_single_leading_slash_is_working_directory_rooted(tmp_path: Path) -> None:
    rules = [ConstraintRule(pattern="/secrets/**", policy=ConstraintPolicy.DENY)]

    assert (
        check_path_constraint(tmp_path / "secrets" / "keys.txt", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.DENY
    )
    assert (
        check_path_constraint(tmp_path / "src" / "secrets" / "keys.txt", rules, tmp_path, ConstraintPolicy.ASK)
        == ConstraintPolicy.ASK
    )


def test_home_directory_anchor(tmp_path: Path) -> None:
    home_dir = tmp_path / "home" / "alice"
    working_dir = tmp_path / "workspace"
    rules = [ConstraintRule(pattern="~/Documents/*.pdf", policy=ConstraintPolicy.ALLOW)]

    assert (
        check_path_constraint(
            home_dir / "Documents" / "report.pdf",
            rules,
            working_dir,
            ConstraintPolicy.ASK,
            home_dir,
        )
        == ConstraintPolicy.ALLOW
    )
    assert (
        check_path_constraint(
            home_dir / "Documents" / "archive" / "report.pdf",
            rules,
            working_dir,
            ConstraintPolicy.ASK,
            home_dir,
        )
        == ConstraintPolicy.ASK
    )
    assert (
        check_path_constraint(
            tmp_path / "other" / "Documents" / "report.pdf",
            rules,
            working_dir,
            ConstraintPolicy.ASK,
            home_dir,
        )
        == ConstraintPolicy.ASK
    )


def test_windows_posix_drive_root_rule_matches_only_that_drive() -> None:
    working_dir = PureWindowsPath("C:/Users/alice/project")
    rules = [ConstraintRule(pattern="//c/**/.env", policy=ConstraintPolicy.DENY)]

    assert (
        check_path_constraint(
            PureWindowsPath("C:/Users/alice/project/.env"),
            rules,
            working_dir,
            ConstraintPolicy.ASK,
        )
        == ConstraintPolicy.DENY
    )
    assert (
        check_path_constraint(
            PureWindowsPath("D:/Users/alice/project/.env"),
            rules,
            working_dir,
            ConstraintPolicy.ASK,
        )
        == ConstraintPolicy.ASK
    )


def test_windows_posix_filesystem_root_rule_matches_all_drives() -> None:
    working_dir = PureWindowsPath("C:/Users/alice/project")
    rules = [ConstraintRule(pattern="//**/.env", policy=ConstraintPolicy.DENY)]

    assert (
        check_path_constraint(
            PureWindowsPath("D:/Users/alice/project/.env"),
            rules,
            working_dir,
            ConstraintPolicy.ASK,
        )
        == ConstraintPolicy.DENY
    )


def test_windows_ruleset_can_mix_posix_and_drive_absolute_rules() -> None:
    working_dir = PureWindowsPath("C:/Users/alice/project")
    rules = [
        ConstraintRule(pattern="//c/Users/alice/public/**", policy=ConstraintPolicy.ALLOW),
        ConstraintRule(pattern="C:/Users/alice/**", policy=ConstraintPolicy.DENY),
    ]

    assert (
        check_path_constraint(
            PureWindowsPath("C:/Users/alice/public/readme.txt"),
            rules,
            working_dir,
            ConstraintPolicy.ASK,
        )
        == ConstraintPolicy.ALLOW
    )
    assert (
        check_path_constraint(
            PureWindowsPath("C:/Users/alice/private/secrets.txt"),
            rules,
            working_dir,
            ConstraintPolicy.ASK,
        )
        == ConstraintPolicy.DENY
    )
    assert (
        check_path_constraint(
            PureWindowsPath("D:/Users/alice/private/secrets.txt"),
            rules,
            working_dir,
            ConstraintPolicy.ASK,
        )
        == ConstraintPolicy.ASK
    )


def test_windows_drive_absolute_rule_matches_only_that_drive() -> None:
    working_dir = PureWindowsPath("C:/Users/alice/project")
    rules = [ConstraintRule(pattern="C:/**/.env", policy=ConstraintPolicy.DENY)]

    assert (
        check_path_constraint(
            PureWindowsPath("C:/Users/alice/project/.env"),
            rules,
            working_dir,
            ConstraintPolicy.ASK,
        )
        == ConstraintPolicy.DENY
    )
    assert (
        check_path_constraint(
            PureWindowsPath("D:/Users/alice/project/.env"),
            rules,
            working_dir,
            ConstraintPolicy.ASK,
        )
        == ConstraintPolicy.ASK
    )


def test_windows_drive_directory_rule_matches_descendants() -> None:
    working_dir = PureWindowsPath("C:/Users/alice/project")
    rules = [ConstraintRule(pattern="C:/Users/", policy=ConstraintPolicy.DENY)]

    assert (
        check_path_constraint(
            PureWindowsPath("C:/Users/alice/secrets.txt"),
            rules,
            working_dir,
            ConstraintPolicy.ASK,
        )
        == ConstraintPolicy.DENY
    )
    assert (
        check_path_constraint(
            PureWindowsPath("C:/Temp/secrets.txt"),
            rules,
            working_dir,
            ConstraintPolicy.ASK,
        )
        == ConstraintPolicy.ASK
    )


def test_windows_drive_backslash_rule_is_absolute() -> None:
    working_dir = PureWindowsPath("C:/Users/alice/project")
    rules = [ConstraintRule(pattern="C:\\Users\\", policy=ConstraintPolicy.DENY)]

    assert (
        check_path_constraint(
            PureWindowsPath("C:/Users/alice/secrets.txt"),
            rules,
            working_dir,
            ConstraintPolicy.ASK,
        )
        == ConstraintPolicy.DENY
    )
