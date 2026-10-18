"""Tests for the release SPDX SBOM generator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "make_sbom.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("make_sbom", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_npm_lock_reads_transitive_packages_and_skips_root() -> None:
    module = _load_script()
    packages = module.parse_npm_lock(
        {
            "packages": {
                "": {"name": "root", "version": "0.0.0"},
                "node_modules/react": {
                    "version": "19.3.1",
                    "license": "MIT",
                    "resolved": "https://registry.example/react.tgz",
                },
                "node_modules/@scope/pkg": {
                    "version": "1.2.3",
                    "license": "Apache-2.0",
                },
            }
        }
    )

    assert [(item.name, item.version) for item in packages] == [
        ("@scope/pkg", "1.2.3"),
        ("react", "19.3.1"),
    ]


def test_spdx_document_has_root_relationships_and_purls() -> None:
    module = _load_script()
    packages = [
        module.Package("pypi", "httpx", "0.28.1", "BSD-3-Clause"),
        module.Package("npm", "@scope/pkg", "1.2.3", "MIT"),
    ]

    document = module.build_document(
        version="0.2.1",
        commit="a" * 40,
        packages=packages,
        created="2026-10-18T00:00:00Z",
    )

    assert document["spdxVersion"] == "SPDX-2.3"
    assert len(document["packages"]) == 3
    assert document["documentDescribes"] == ["SPDXRef-Package-hunter1"]
    assert {item["relationshipType"] for item in document["relationships"]} == {
        "DEPENDS_ON",
        "DESCRIBES",
    }
    locators = {
        item["externalRefs"][0]["referenceLocator"]
        for item in document["packages"]
        if "externalRefs" in item
    }
    assert locators == {
        "pkg:npm/%40scope%2Fpkg@1.2.3",
        "pkg:pypi/httpx@0.28.1",
    }


def test_spdx_ids_are_stable_and_safe() -> None:
    module = _load_script()
    assert module._spdx_id("npm", "@scope/pkg", "1.2.3") == "SPDXRef-npm-scope-pkg-1.2.3"
