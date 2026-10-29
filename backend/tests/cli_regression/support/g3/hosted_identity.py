"""The product-identity check of a hosted branch commit (rf4c D7, review S1/T3), as one function the local pre-push check and the tests use. The workflows carry the same four commands as shell lines
(`Product identity against the freeze head`); `IDENTITY_LINES` is that text, and `test_g3h_identity.py` pins that every draft's step is exactly it, so the two cannot drift.

    hosted_identity.py REPO FREEZE_H WORKFLOW_REL [HEAD] [--no-ignored]      exit 0 = the commit is the freeze head plus exactly the one workflow file; 2 = problems are printed

1. the freeze head exists in the checkout;
2. `native`, `src` and `backend/agent_team_backend` equal the freeze head's (`git diff --exit-code`);
3. nothing is untracked, modified or IGNORED there (`git status --porcelain --ignored`: an ignored file is bytes the diff cannot see); `--no-ignored` drops `--ignored` for a local worktree that has build output;
4. `git diff --name-only --no-renames FREEZE_H HEAD` prints EXACTLY this workflow's own path (before: "nothing outside .github/workflows", which let a second workflow file ride along).
Standard library only. Nothing here writes to a repository.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

PRODUCT_PATHS = ("native", "src", "backend/agent_team_backend")
WORKFLOWS_DIR = ".github/workflows"      # the one place the workflow files live; written in the drafts' text only
IDENTITY_LINES = ('git cat-file -e "$FREEZE_H^{commit}"',
                  'git diff --exit-code "$FREEZE_H" -- native src backend/agent_team_backend',
                  'test -z "$(git status --porcelain --ignored -- native src backend/agent_team_backend)"',
                  'test "$(git diff --name-only --no-renames "$FREEZE_H" "$GITHUB_SHA")" = "{workflow}"')


def workflow_path(file_name: str) -> str:
    return f"{WORKFLOWS_DIR}/{file_name}"


def default_git(repo: str | Path) -> Callable[[list[str]], tuple[int, str]]:
    def run(args: list[str]) -> tuple[int, str]:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout
    return run


def problems(git: Callable[[list[str]], tuple[int, str]], freeze_h: str, workflow_rel: str, head: str = "HEAD", *, ignored: bool = True) -> list[str]:
    out = []
    if git(["cat-file", "-e", f"{freeze_h}^{{commit}}"])[0] != 0:
        return [f"the freeze head {freeze_h[:12]} is not in this checkout"]
    if git(["diff", "--exit-code", freeze_h, "--", *PRODUCT_PATHS])[0] != 0:
        out.append("a product path differs from the freeze head")
    status_args = ["status", "--porcelain", *(["--ignored"] if ignored else []), "--", *PRODUCT_PATHS]
    code, text = git(status_args)
    if code != 0 or text.strip():
        out.append("a product path has untracked, modified or ignored files")
    code, text = git(["diff", "--name-only", "--no-renames", freeze_h, head])
    if code != 0:
        out.append("the changed-file list cannot be read")
    elif text.rstrip("\n").split("\n") != [workflow_rel]:
        out.append(f"the commit changes {[line for line in text.split(chr(10)) if line]}, not exactly [{workflow_rel!r}]")
    return out


def main(argv: list[str]) -> int:
    flags = [a for a in argv if a.startswith("--")]
    args = [a for a in argv if not a.startswith("--")]
    if len(args) not in (3, 4) or set(flags) - {"--no-ignored"}:
        print(__doc__, file=sys.stderr)
        return 64
    found = problems(default_git(args[0]), args[1], args[2], args[3] if len(args) == 4 else "HEAD", ignored="--no-ignored" not in flags)
    for line in found:
        print(line)
    return 0 if not found else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
