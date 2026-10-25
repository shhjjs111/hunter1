#!/usr/bin/env python
"""Generate an SPDX 2.3 SBOM for the exact release environment."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from hunter1 import __version__  # noqa: E402

REPOSITORY_URL = "https://github.com/shhjjs111/hunter1"
DEFAULT_NPM_LOCK = ROOT / "frontend" / "package-lock.json"
DEFAULT_OUTPUT = ROOT / "dist" / "sbom.spdx.json"


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


@dataclass(frozen=True, order=True)
class Package:
    """One dependency discovered from a locked ecosystem."""

    ecosystem: str
    name: str
    version: str
    license: str = "NOASSERTION"
    download_location: str = "NOASSERTION"


@dataclass(frozen=True)
class Artifact:
    """A shipped build artifact (APK / zip), identified by its real bytes."""

    name: str
    path: Path


def _python_name(name: str) -> str:
    """PEP 503 normalized name, also suitable for a PyPI purl."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _spdx_id(ecosystem: str, name: str, version: str) -> str:
    raw = f"SPDXRef-{ecosystem}-{name}-{version}"
    safe = re.sub(r"[^A-Za-z0-9.-]+", "-", raw)
    return re.sub(r"-+", "-", safe).strip("-")


def _license_value(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "NOASSERTION"


def parse_npm_lock(payload: dict[str, object]) -> list[Package]:
    """Read npm lockfile v2/v3 packages, excluding the private root project."""
    packages = payload.get("packages")
    if not isinstance(packages, dict):
        return []

    found: dict[tuple[str, str], Package] = {}
    for path, raw in packages.items():
        if not isinstance(path, str) or not path or not isinstance(raw, dict):
            continue
        version = raw.get("version")
        if not isinstance(version, str) or not version:
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            marker = "node_modules/"
            name = path.rsplit(marker, 1)[-1]
        resolved = raw.get("resolved")
        package = Package(
            ecosystem="npm",
            name=name,
            version=version,
            license=_license_value(raw.get("license")),
            download_location=(
                resolved
                if isinstance(resolved, str) and resolved.startswith("https://")
                else "NOASSERTION"
            ),
        )
        found[(package.name, package.version)] = package
    return sorted(found.values())


def python_packages() -> list[Package]:
    """Inventory the active Python environment, excluding the project itself."""
    found: dict[tuple[str, str], Package] = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name", "")
        version = distribution.version
        if not name or not version or _python_name(name) == "hunter1":
            continue
        normalized = _python_name(name)
        found[(normalized, version)] = Package(
            ecosystem="pypi",
            name=normalized,
            version=version,
            # License metadata is often prose, not an SPDX expression. Only the
            # standardized License-Expression field is safe for licenseDeclared.
            license=_license_value(distribution.metadata.get("License-Expression")),
        )
    return sorted(found.values())


def parse_artifact_spec(spec: str) -> Artifact:
    """`name=path` → Artifact；路径必须存在，否则发布清单会指向一个假文件。"""
    name, separator, raw_path = spec.partition("=")
    if not separator or not name.strip() or not raw_path.strip():
        raise ValueError(f"artifact 必须是 name=path，收到 {spec!r}")
    path = Path(raw_path)
    if not path.is_file():
        raise ValueError(f"artifact 不存在：{path}")
    return Artifact(name.strip(), path)


def _artifact_package(artifact: Artifact, version: str) -> dict[str, object]:
    with artifact.path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    return {
        "SPDXID": _spdx_id("artifact", artifact.name, version),
        "name": artifact.name,
        "versionInfo": version,
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "checksums": [{"algorithm": "SHA256", "checksumValue": digest}],
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "NOASSERTION",
        "copyrightText": "NOASSERTION",
        "primaryPackagePurpose": "APPLICATION",
    }


def _external_ref(package: Package) -> dict[str, str]:
    if package.ecosystem == "npm":
        locator = f"pkg:npm/{quote(package.name, safe='')}@{quote(package.version, safe='')}"
    else:
        locator = f"pkg:pypi/{quote(package.name, safe='')}@{quote(package.version, safe='')}"
    return {
        "referenceCategory": "PACKAGE-MANAGER",
        "referenceType": "purl",
        "referenceLocator": locator,
    }


def _dependency_package(package: Package) -> dict[str, object]:
    spdx_id = _spdx_id(package.ecosystem, package.name, package.version)
    return {
        "SPDXID": spdx_id,
        "name": package.name,
        "versionInfo": package.version,
        "downloadLocation": package.download_location,
        "filesAnalyzed": False,
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": package.license,
        "copyrightText": "NOASSERTION",
        "primaryPackagePurpose": "LIBRARY",
        "externalRefs": [_external_ref(package)],
    }


def build_document(
    *,
    version: str,
    commit: str,
    packages: list[Package],
    created: str,
    artifacts: list[Artifact] | None = None,
) -> dict[str, object]:
    """Build a deterministic SPDX document for the supplied package inventory."""
    root_id = "SPDXRef-Package-hunter1"
    unique = {(package.ecosystem, package.name, package.version): package for package in packages}
    ordered = [unique[key] for key in sorted(unique)]
    dependency_packages = [_dependency_package(package) for package in ordered]
    relationships: list[dict[str, str]] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": root_id,
        }
    ]
    relationships.extend(
        {
            "spdxElementId": root_id,
            "relationshipType": "DEPENDS_ON",
            "relatedSpdxElement": str(package["SPDXID"]),
        }
        for package in dependency_packages
    )
    artifact_packages = [_artifact_package(item, version) for item in artifacts or []]
    relationships.extend(
        {
            "spdxElementId": root_id,
            "relationshipType": "CONTAINS",
            "relatedSpdxElement": str(package["SPDXID"]),
        }
        for package in artifact_packages
    )
    return {
        "SPDXID": "SPDXRef-DOCUMENT",
        "spdxVersion": "SPDX-2.3",
        "creationInfo": {
            "created": created,
            "creators": ["Tool: hunter1-make-sbom-1"],
        },
        "name": f"hunter1-{version}",
        "dataLicense": "CC0-1.0",
        "documentNamespace": f"{REPOSITORY_URL}/sbom/{version}/{commit}",
        "documentDescribes": [root_id],
        "packages": [
            {
                "SPDXID": root_id,
                "name": "hunter1",
                "versionInfo": version,
                "downloadLocation": f"{REPOSITORY_URL}/tree/v{version}",
                "filesAnalyzed": False,
                "licenseConcluded": "MIT",
                "licenseDeclared": "MIT",
                "copyrightText": "NOASSERTION",
                "primaryPackagePurpose": "APPLICATION",
            },
            *dependency_packages,
            *artifact_packages,
        ],
        "relationships": relationships,
    }


def _git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).strip()


def main(argv: Sequence[str] | None = None) -> int:
    _configure_console()
    parser = argparse.ArgumentParser(prog="make_sbom.py", description="生成 SPDX 2.3 SBOM")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="SPDX JSON 输出路径")
    parser.add_argument(
        "--npm-lock",
        default=str(DEFAULT_NPM_LOCK),
        help="frontend package-lock.json 路径",
    )
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="随版本发布的产物（可重复）；会写入 SPDX 包与其 SHA256",
    )
    args = parser.parse_args(argv)

    try:
        npm_lock = json.loads(Path(args.npm_lock).read_text(encoding="utf-8"))
        if not isinstance(npm_lock, dict):
            raise ValueError("package-lock.json 顶层必须是对象")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"读取 npm lock 失败：{exc}", file=sys.stderr)
        return 2

    try:
        artifacts = [parse_artifact_spec(spec) for spec in args.artifact]
    except ValueError as exc:
        print(f"artifact 参数不合法：{exc}", file=sys.stderr)
        return 2

    packages = python_packages() + parse_npm_lock(npm_lock)
    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    document = build_document(
        version=__version__,
        commit=_git_commit(),
        packages=packages,
        created=created,
        artifacts=artifacts,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"SPDX SBOM：{output}")
    print(f"  version：{__version__}")
    document_packages = document["packages"]
    assert isinstance(document_packages, list)
    print(f"  packages：{len(document_packages) - 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
