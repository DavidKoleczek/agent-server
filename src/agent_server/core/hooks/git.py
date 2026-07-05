import subprocess


def _run_git(working_directory: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", working_directory, *args],
        capture_output=True,
        text=True,
    )


def is_git_repo(working_directory: str) -> str:
    result = _run_git(working_directory, ["rev-parse", "--is-inside-work-tree"])
    return "Yes" if result.returncode == 0 else "No"


def current_branch(working_directory: str) -> str | None:
    if is_git_repo(working_directory) == "No":
        return None
    result = _run_git(working_directory, ["branch", "--show-current"])
    branch = result.stdout.strip()
    return branch if branch else None


def main_branch(working_directory: str) -> str | None:
    if is_git_repo(working_directory) == "No":
        return None
    # Try to get the default branch from the remote HEAD reference.
    # This returns something like "refs/remotes/origin/main".
    result = _run_git(working_directory, ["symbolic-ref", "refs/remotes/origin/HEAD"])
    if result.returncode == 0:
        return result.stdout.strip().split("/")[-1]
    # Fallback: check if common default branch names exist locally.
    for branch in ["main", "master"]:
        result = _run_git(working_directory, ["show-ref", "--verify", f"refs/heads/{branch}"])
        if result.returncode == 0:
            return branch
    return None


def git_status(working_directory: str, limit: int = 50) -> str | None:
    if is_git_repo(working_directory) == "No":
        return None
    result = _run_git(working_directory, ["status", "--porcelain"])
    lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
    if not lines:
        return "Clean"
    output_lines = []
    for line in lines[:limit]:
        output_lines.append(line)
    if len(lines) > limit:
        output_lines.append(f"And {len(lines) - limit} more files...")
    return "\n".join(output_lines)


def recent_commits(working_directory: str, n: int = 5) -> str | None:
    if is_git_repo(working_directory) == "No":
        return None
    result = _run_git(working_directory, ["log", "--oneline", f"-n{n}"])
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()
