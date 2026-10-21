"""Static guards for GitHub workflow and supply-chain hygiene."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)
_PINNED = re.compile(r"^[^@]+@[0-9a-f]{40}$")
_URL = re.compile(r"https?://[^\s\"']+")

#: 锁文件里出现这些主机说明本地镜像配置渗进了公开仓库。CI 会按 `resolved`
#: 地址下载 tarball；第三方镜像一旦不可用或被篡改，门禁/构建就不再可复现。
_NPM_MIRROR_HOSTS = (
    "registry.npmmirror.com",
    "registry.npm.taobao.org",
    "registry.yarnpkg.com",
    "mirrors.cloud.tencent.com",
)


def judge_third_party_npm_registries(text: str) -> list[str]:
    """Return mirror hosts embedded in a lockfile's resolved URLs.

    Parse the URL and compare its hostname exactly; substring matching would
    treat `evil-registry.npmjs.org.attacker.tld` as trusted.
    """
    hosts = {(urlsplit(match.group(0)).hostname or "").lower() for match in _URL.finditer(text)}
    return sorted(host for host in hosts if host in _NPM_MIRROR_HOSTS)


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
        if not re.search(r"^permissions:\s*(?:\{\s*\})?\s*$", text, re.MULTILINE):
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
    assert "Android APK" in text
    assert "assembleRelease" in text
    assert "hunter1-android-" in text
    assert "verify_apk.py" in text
    assert "ANDROID_KEYSTORE_BASE64" in text
    assert "dist/*.apk" in text


def test_dependabot_covers_android_gradle() -> None:
    text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert "package-ecosystem: gradle" in text
    assert "directory: /frontend/android" in text


def test_codeql_scans_workflow_files() -> None:
    text = (WORKFLOWS / "codeql.yml").read_text(encoding="utf-8")
    assert "- actions" in text, "CodeQL 没扫 actions，工作流改动会绕过静态分析"


def test_required_quality_status_context_stays_stable() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert 'python-version: ["3.12"]' in text, (
        "quality job 的 matrix 必须保持 3.12，否则必需状态检查名称会漂移"
    )


def test_npm_lockfiles_use_public_registry() -> None:
    lockfiles = (
        ROOT / "frontend" / "package-lock.json",
        ROOT / "frontend" / "tools" / "contract-codegen" / "package-lock.json",
    )
    violations = {}
    for path in lockfiles:
        text = path.read_text(encoding="utf-8")
        hits = judge_third_party_npm_registries(text)
        data = json.loads(text)
        resolved = [
            package["resolved"]
            for package in data.get("packages", {}).values()
            if isinstance(package, dict) and isinstance(package.get("resolved"), str)
        ]
        assert resolved, f"{path.name} 没有 resolved 地址"
        hosts = {(urlsplit(url).hostname or "").lower() for url in resolved}
        if hits or hosts != {"registry.npmjs.org"}:
            violations[path.relative_to(ROOT).as_posix()] = sorted(hits or hosts)
    assert not violations, f"锁文件包含第三方 npm 镜像：{violations}"


def test_registry_judge_fires_on_mirror_hosts() -> None:
    assert judge_third_party_npm_registries(
        "https://registry.npmmirror.com/pkg/-/pkg-1.0.0.tgz"
    ) == ["registry.npmmirror.com"]
    assert judge_third_party_npm_registries("https://registry.npmjs.org/pkg/-/pkg-1.0.0.tgz") == []


def test_gradle_wrapper_pins_distribution_checksum() -> None:
    wrapper = ROOT / "frontend" / "android" / "gradle" / "wrapper" / "gradle-wrapper.properties"
    text = wrapper.read_text(encoding="utf-8")
    assert "distributionUrl=https\\://services.gradle.org/distributions/" in text
    assert re.search(r"^distributionSha256Sum=[0-9a-f]{64}$", text, re.MULTILINE), (
        "Gradle wrapper 必须固定发行包的 SHA256，避免下载链被替换"
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
