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


def test_release_workflow_is_tag_driven_and_attested() -> None:
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")

    assert 'tags: ["v*"]' in text
    assert "id-token: write" in text
    assert "attestations: write" in text
    assert "actions/attest-build-provenance@" in text
    assert "actions/attest@" in text
    assert "sbom-path: dist/sbom.spdx.json" in text
    assert "make_sbom.py" in text
    assert "SHA256SUMS.txt" in text
    assert "--draft" in text
    assert 'gh release edit "$TAG" --draft=false' not in text, (
        "Release workflow 只创建草稿；安卓 APK 需在人工核对后随草稿一起发布"
    )
    assert "安卓 APK" in text


def test_required_quality_status_context_stays_stable() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert 'python-version: ["3.12"]' in text, (
        "quality job 的 matrix 必须保持 3.12，否则必需状态检查名称会漂移"
    )


def test_python_dependencies_are_locked_and_ci_is_frozen() -> None:
    assert (ROOT / "backend" / "uv.lock").is_file()
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "uv lock" not in text, "CI 只消费锁文件；更新 lock 必须显式提交"
    assert "uv sync" in text
    assert "--frozen" in text


def test_legacy_manual_release_is_blocked_by_default() -> None:
    for path in (ROOT / "release.cmd", ROOT / "scripts" / "release.sh"):
        text = path.read_text(encoding="utf-8")
        assert "HUNTER1_ALLOW_LEGACY_RELEASE" in text, (
            f"{path.relative_to(ROOT)} 允许无显式确认的手工发布"
        )
