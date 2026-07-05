"""Ripgrep binary discovery and execution utilities."""

from pathlib import Path
import platform
import shutil
import subprocess


def get_platform_key() -> str:
    """Get platform identifier for binary selection.

    Returns:
        Platform key like 'linux-x86_64', 'darwin-arm64', 'windows-x86_64'.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("aarch64", "arm64"):
        arch = "aarch64" if system == "linux" else "arm64"
    else:
        arch = machine

    return f"{system}-{arch}"


def find_ripgrep() -> Path | None:
    """Find ripgrep binary using fallback strategy.

    Priority:
        1. System 'rg' command
        2. System 'ripgrep' command
        3. Bundled binary for current platform

    Returns:
        Path to ripgrep binary, or None if not found.
    """
    for cmd in ("rg", "ripgrep"):
        path = shutil.which(cmd)
        if path:
            return Path(path)

    platform_key = get_platform_key()
    bin_dir = Path(__file__).parent / "bin" / platform_key
    binary = bin_dir / "rg.exe" if platform.system().lower() == "windows" else bin_dir / "rg"

    if binary.exists() and binary.is_file():
        return binary

    return None


def run_ripgrep(args: list[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run ripgrep with given arguments.

    Args:
        args: Command line arguments (excluding the rg command itself).
        cwd: Working directory for the search.
        timeout: Timeout in seconds (default 30).

    Returns:
        CompletedProcess with stdout, stderr, and returncode.

    Raises:
        FileNotFoundError: If ripgrep binary cannot be found.
        subprocess.TimeoutExpired: If command times out.
    """
    rg_path = find_ripgrep()
    if rg_path is None:
        raise FileNotFoundError("ripgrep binary not found")

    return subprocess.run(
        [str(rg_path), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
