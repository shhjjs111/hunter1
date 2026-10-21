#!/usr/bin/env python
"""Verify a release APK before it enters the release supply chain.

Checks the identity (package/version), Android flags (debuggable/allowBackup),
permission surface, signature, and certificate continuity. Prints the APK
SHA-256 so the caller can add it to SHA256SUMS.txt.

Usage:
    python scripts/verify_apk.py --apk dist/hunter1-android-0.4.1.apk \
        --version 0.4.1 --expected-cert-file frontend/android/EXPECTED_CERT_SHA256
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CERT_FILE = ROOT / "frontend" / "android" / "EXPECTED_CERT_SHA256"
EXPECTED_PACKAGE = "com.hunter1.app"

_PACKAGE_RE = re.compile(r"package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'")
_PERMISSION_RE = re.compile(r"uses-permission: name='([^']+)'")
# aapt2 xmltree 的布尔值是 `=(type 0x12)0xffffffff` / `0x0`，部分版本则是
# 字面量 `true` / `false`。两种形状都要能读，读不懂时必须失败而不是当作 false。
_ATTR_RE = re.compile(r":([A-Za-z]+)\([^)]*\)=(?:\(type 0x[0-9a-f]+\))?(\w+)", re.IGNORECASE)
_CERT_RE = re.compile(r"certificate SHA-256 digest:\s*([0-9a-fA-F:]+)")
_CERT_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BOOL_TRUE = frozenset({"true", "1", "0x1", "0xffffffff"})
_BOOL_FALSE = frozenset({"false", "0", "0x0"})


class VerifyError(RuntimeError):
    """The APK does not satisfy the release contract."""


def parse_badging(text: str) -> dict[str, Any]:
    match = _PACKAGE_RE.search(text)
    if match is None:
        raise VerifyError("aapt2 输出里找不到 package 行")
    name, version_code, version_name = match.groups()
    permissions = sorted(set(_PERMISSION_RE.findall(text)))
    return {
        "package": name,
        "versionCode": int(version_code),
        "versionName": version_name,
        "permissions": permissions,
    }


def parse_manifest_flags(text: str) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for name, value in _ATTR_RE.findall(text):
        if name in {"debuggable", "allowBackup", "usesCleartextTraffic"}:
            lowered = value.lower()
            if lowered in _BOOL_TRUE:
                flags[name] = True
            elif lowered in _BOOL_FALSE:
                flags[name] = False
            else:
                # 安全属性解析不了时必须失败，不能默认成 False 放行。
                raise VerifyError(f"无法解析 AndroidManifest 属性 {name}={value!r}")
    return flags


def check_manifest_flags(flags: dict[str, bool]) -> None:
    """发布 APK 的安全属性必须显式满足，缺失也按失败处理。"""
    if flags.get("debuggable") is True:
        raise VerifyError("APK 是可调试构建（debuggable=true）")
    if flags.get("allowBackup") is not False:
        # allowBackup 未声明时 Android 默认允许云备份；必须显式写 false。
        raise VerifyError("APK 没有显式关闭云备份（allowBackup=false），本地 Key 会离开设备")


def parse_certificate(text: str) -> str:
    match = _CERT_RE.search(text)
    if match is None:
        raise VerifyError("apksigner 输出里找不到 SHA-256 证书指纹")
    return match.group(1).replace(":", "").lower()


def load_expected_cert(path: Path) -> str:
    """读期望证书指纹；文件缺失或格式不对时必须失败，不能跳过连续性校验。"""
    if not path.is_file():
        raise VerifyError(f"找不到期望证书指纹文件：{path}")
    value = path.read_text(encoding="utf-8").strip().replace(":", "").lower()
    if not _CERT_SHA256_RE.fullmatch(value):
        raise VerifyError(f"期望证书指纹文件不是 64 位 SHA-256：{path}")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_key(path: Path) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", path.name))


def find_build_tool(name: str) -> Path:
    roots = [
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
        str(ROOT / ".tools" / "android" / "sdk"),
    ]
    for root in roots:
        if not root:
            continue
        build_tools = Path(root) / "build-tools"
        if not build_tools.is_dir():
            continue
        for version in sorted(build_tools.iterdir(), key=_version_key, reverse=True):
            for candidate in (version / name, version / f"{name}.exe", version / f"{name}.bat"):
                if candidate.is_file():
                    return candidate
    found = shutil.which(name)
    if found:
        return Path(found)
    raise VerifyError(f"找不到 {name}：设置 ANDROID_HOME，或用 --{name} 指定路径")


def _java_home() -> str | None:
    """CI 用 setup-java；本机优先用仓库自带的 JDK。"""
    configured = os.environ.get("JAVA_HOME")
    if configured:
        return configured
    for name in ("java.exe", "java"):
        for candidate in (ROOT / ".tools" / "jdks").glob(f"**/bin/{name}"):
            return str(candidate.parent.parent)
    return None


def run_tool(tool: Path, *args: str) -> str:
    command = [str(tool), *args]
    if os.name == "nt" and tool.suffix.lower() in {".bat", ".cmd"}:
        command = ["cmd", "/c", *command]
    env = os.environ.copy()
    java_home = _java_home()
    if java_home is not None:
        env.setdefault("JAVA_HOME", java_home)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=env)
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise VerifyError(f"{tool.name} 失败（exit {result.returncode}）：\n{output}")
    return output


def verify(
    apk: Path,
    *,
    version: str | None,
    version_code: int | None,
    expected_cert: str | None,
    aapt2: Path,
    apksigner: Path,
) -> dict[str, Any]:
    if not apk.is_file():
        raise VerifyError(f"APK 不存在：{apk}")

    badging = parse_badging(run_tool(aapt2, "dump", "badging", str(apk)))
    flags = parse_manifest_flags(
        run_tool(aapt2, "dump", "xmltree", "--file", "AndroidManifest.xml", str(apk))
    )
    certificate = parse_certificate(run_tool(apksigner, "verify", "--print-certs", str(apk)))

    if badging["package"] != EXPECTED_PACKAGE:
        raise VerifyError(f"包名不符：{badging['package']}（应为 {EXPECTED_PACKAGE}）")
    if version is not None and badging["versionName"] != version:
        raise VerifyError(f"versionName 不符：{badging['versionName']}（应为 {version}）")
    if version_code is not None and badging["versionCode"] != version_code:
        raise VerifyError(f"versionCode 不符：{badging['versionCode']}（应为 {version_code}）")
    check_manifest_flags(flags)

    allowed = {
        "android.permission.INTERNET",
        f"{EXPECTED_PACKAGE}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION",
    }
    extra = sorted(set(badging["permissions"]) - allowed)
    if extra:
        raise VerifyError(f"APK 申请了额外权限：{extra}")

    if expected_cert is not None and certificate != expected_cert.replace(":", "").lower():
        raise VerifyError(f"签名证书指纹不符：{certificate}（应为 {expected_cert}）")

    return {
        "apk": apk,
        "sha256": file_sha256(apk),
        "package": badging["package"],
        "versionCode": badging["versionCode"],
        "versionName": badging["versionName"],
        "certificate": certificate,
        "permissions": badging["permissions"],
        "allowBackup": flags.get("allowBackup", False),
        "debuggable": flags.get("debuggable", False),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_apk.py", description="校验发布 APK")
    parser.add_argument("--apk", required=True)
    parser.add_argument("--version", default=None)
    parser.add_argument("--version-code", type=int, default=None)
    parser.add_argument("--expected-cert-file", default=str(DEFAULT_CERT_FILE))
    parser.add_argument("--aapt2", default=None)
    parser.add_argument("--apksigner", default=None)
    args = parser.parse_args(argv)

    try:
        expected_cert = load_expected_cert(Path(args.expected_cert_file))
        result = verify(
            Path(args.apk),
            version=args.version,
            version_code=args.version_code,
            expected_cert=expected_cert,
            aapt2=Path(args.aapt2) if args.aapt2 else find_build_tool("aapt2"),
            apksigner=Path(args.apksigner) if args.apksigner else find_build_tool("apksigner"),
        )
    except VerifyError as exc:
        print(f"APK 校验失败：{exc}", file=sys.stderr)
        return 2

    print(f"APK：{result['apk']}")
    print(f"  package：{result['package']}")
    print(f"  version：{result['versionName']} ({result['versionCode']})")
    print(f"  permissions：{', '.join(result['permissions'])}")
    print(f"  certificate SHA-256：{result['certificate']}")
    print(f"  sha256：{result['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
