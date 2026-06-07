---
name: update-dependencies
description: Updates project dependencies and updates the reference repositories under reference/.
disable-model-invocation: true
---

# Python dependencies

I need you to update the dependencies in `pyproject.toml`

The process to go through is:
1. Notice how we do versioning. We make sure that we do not auto upgrade to the major version.
For example, "openai[aiohttp]>=2.9,<3.0", means we will never upgrade to v3.x.
You will just be updating minor versions. Note that for dev dependencies we don't need to pin to be less than the major version.
1. For each dependency, go to its pypi release history site. For example, for the openai package that is: https://pypi.org/project/openai/#history Get the latest release version.
1. Bump each dependency that is not at its latest version in `pyproject.toml`.
Never include patch versions in the lower bound -- only specify major.minor. If only the patch version changed, no update is needed.
For example, if the current version is `>=1.5,<2.0` and the latest is 1.11.3, change to `>=1.11,<2.0` (bump the minor, omit the patch).
If the current version is `>=1.5,<2.0` and the latest is 1.5.8, no change is needed (only the patch changed).
1. If you notice a major version upgrade (ex v2 to v3), let the user know of each of those cases, but do not make the change yourself.
1. Make sure all the checks still pass, but do not run any tests.
1. Update the `uv_build` version in the `pyproject.toml` to the latest version. You can find the latest version here: https://pypi.org/project/uv-build/#history
1. Run `uv sync -U --all-extras --all-groups` to update the lock file. Do this regardless of whether you updated the pyproject.toml or not.


# Reference Repos Update

Keeps `reference/<repo>` in sync with the upstream version this project depends on. Run all commands from the repo root. All clones are shallow (`--depth 1 --single-branch`).

Repos:
- `reference/agent-tui` from https://github.com/DavidKoleczek/agent-tui at branch `vopentui`.

## Update commands

PowerShell:

```powershell
$url    = "<repo URL>"
$branch = "<branch>"
$dst    = "<dst path, e.g. reference\opencode>"
if (Test-Path "$dst\.git") {
    git -C $dst fetch --depth 1 origin $branch
    # Move HEAD to the freshly fetched tip; a shallow clone has no local branch ref to fast-forward.
    git -C $dst reset --hard FETCH_HEAD
} else {
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    git clone --depth 1 --branch $branch --single-branch $url $dst
}
```

Bash:

```bash
url="<repo URL>"
branch="<branch>"
dst="<dst path, e.g. reference/opencode>"
if [ -d "$dst/.git" ]; then
    git -C "$dst" fetch --depth 1 origin "$branch"
    git -C "$dst" reset --hard FETCH_HEAD
else
    rm -rf "$dst"
    git clone --depth 1 --branch "$branch" --single-branch "$url" "$dst"
fi
```
