"""Static guards for GitHub workflow and supply-chain hygiene."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)
_PINNED = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def judge_unpinned_actions(text: str) -> list[str]:
    """Return remote action references that are not pinned to a full commit SHA."""
    violations = []
    for match in _USES.finditer(text):
        ref = match.group(1)
        if ref.startswith("./") or ref.startswith("docker://"):
            continue
        if not _PINNED.fullmatch(ref):
            violations.append(ref)
    return violations


def test_all_remote_actions_are_pinned_to_full_commit_sha() -> None:
    violations = {
        path.relative_to(ROOT).as_posix(): judge_unpinned_actions(path.read_text(encoding="utf-8"))
        for path in sorted(WORKFLOWS.glob("*.y*ml"))
    }
    violations = {path: refs for path, refs in violations.items() if refs}
    assert not violations, f"remote Actions must be SHA-pinned: {violations}"


def test_every_workflow_declares_least_privilege_defaults() -> None:
    missing = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        if not re.search(r"^permissions:\s*$", text, re.MULTILINE):
            missing.append(path.relative_to(ROOT).as_posix())
    assert not missing, f"workflows missing top-level permissions: {missing}"


def test_unpinned_action_judge_fires_on_mutable_refs() -> None:
    sample = """
steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-python@main
"""
    assert judge_unpinned_actions(sample) == [
        "actions/checkout@v4",
        "actions/setup-python@main",
    ]


def test_unpinned_action_judge_accepts_sha_and_local_actions() -> None:
    sample = """
steps:
  - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
  - uses: ./.github/actions/local
"""
    assert judge_unpinned_actions(sample) == []
