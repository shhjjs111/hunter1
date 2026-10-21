"""Tests for the release APK verifier (pure parsing paths)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_apk.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("verify_apk", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BADGING = """package: name='com.hunter1.app' versionCode='4001' versionName='0.4.1'
uses-permission: name='android.permission.INTERNET'
uses-permission: name='com.hunter1.app.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION'
"""

MANIFEST = """
A: http://schemas.android.com/apk/res/android:allowBackup(0x01010280)=(type 0x12)0x0
A: http://schemas.android.com/apk/res/android:usesCleartextTraffic(0x010104ec)=(type 0x12)0xffffffff
A: http://schemas.android.com/apk/res/android:debuggable(0x0101000f)=(type 0x12)0xffffffff
"""

CERTIFICATE = "Signer #1 certificate SHA-256 digest: 87:F0:80:04:B2:0D"


def test_parse_badging_extracts_identity_and_permissions() -> None:
    module = _load_script()
    parsed = module.parse_badging(BADGING)
    assert parsed["package"] == "com.hunter1.app"
    assert parsed["versionCode"] == 4001
    assert parsed["versionName"] == "0.4.1"
    assert parsed["permissions"] == [
        "android.permission.INTERNET",
        "com.hunter1.app.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION",
    ]


def test_parse_manifest_flags_reads_security_relevant_attributes() -> None:
    module = _load_script()
    flags = module.parse_manifest_flags(MANIFEST)
    assert flags == {"allowBackup": False, "usesCleartextTraffic": True, "debuggable": True}


def test_parse_certificate_normalizes_colons_and_case() -> None:
    module = _load_script()
    assert module.parse_certificate(CERTIFICATE) == "87f08004b20d"


def test_verify_rejects_debuggable_and_backup_enabled() -> None:
    module = _load_script()
    flags = module.parse_manifest_flags(MANIFEST)
    with pytest.raises(module.VerifyError, match="debuggable=true"):
        module.check_manifest_flags(flags)
    with pytest.raises(module.VerifyError, match="allowBackup"):
        module.check_manifest_flags({"allowBackup": True})
    with pytest.raises(module.VerifyError, match="没有显式关闭"):
        module.check_manifest_flags({})
    module.check_manifest_flags({"allowBackup": False})


def test_unparseable_security_flag_fails_closed() -> None:
    module = _load_script()
    with pytest.raises(module.VerifyError, match="无法解析"):
        module.parse_manifest_flags("A: x:allowBackup(0x1)=maybe")


def test_expected_certificate_file_must_exist_and_be_a_sha256(tmp_path: Path) -> None:
    module = _load_script()

    with pytest.raises(module.VerifyError, match="找不到"):
        module.load_expected_cert(tmp_path / "missing.txt")

    bad = tmp_path / "bad.txt"
    bad.write_text("not-a-fingerprint", encoding="utf-8")
    with pytest.raises(module.VerifyError, match="64 位 SHA-256"):
        module.load_expected_cert(bad)

    good = tmp_path / "good.txt"
    expected = "87:F0:" + "00:" * 29 + "FF"
    good.write_text(expected, encoding="utf-8")
    assert module.load_expected_cert(good) == expected.replace(":", "").lower()
