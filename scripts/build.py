"""构建可分发产物（PyInstaller）。

    ./.tools/python/python.exe scripts/build.py          # 构建 + 校验
    ./.tools/python/python.exe scripts/build.py --zip    # 再打一个 zip

**构建之后一定要校验**：exe 存在不代表能跑。最常见的事故是模板没打进去 ——
产物能启动，但每个页面都 500。所以这里把「模板在不在」当成硬门禁，
不合格就非零退出，别让一个坏包流出去。

不做的事：不签名、不上传。分发渠道是另一回事（见 DEVELOPMENT-PLAN §6.4）。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP_NAME = "hunter1"
TEMPLATE_SUBDIR = Path("_internal") / "hunter1" / "web" / "templates"

# 规划 §3.4 给的分发体积上限
SIZE_BUDGET_MB = 200


def _exe_name() -> str:
    return f"{APP_NAME}.exe" if os.name == "nt" else APP_NAME


def _require_pyinstaller() -> None:
    if importlib.util.find_spec("PyInstaller") is None:
        raise SystemExit(
            "缺少 PyInstaller。先装：\n"
            f'  {sys.executable} -m pip install -e ".[build]"\n'
            "（或直接：pip install --index-url https://pypi.org/simple pyinstaller）"
        )


def _run_pyinstaller() -> None:
    # --clean 清掉 PyInstaller 的缓存目录，避免拿到上一次的残留
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--log-level",
        "WARN",
        "hunter1.spec",
    ]
    print("== 构建 ==")
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        raise SystemExit(f"PyInstaller 失败（exit {result.returncode}）")


def _dir_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def verify(dist_dir: Path) -> list[str]:
    """产物硬门禁。返回问题列表（空 = 合格）。"""
    problems: list[str] = []

    exe = dist_dir / _exe_name()
    if not exe.is_file():
        problems.append(f"缺少可执行文件：{exe.name}")

    templates = dist_dir / TEMPLATE_SUBDIR
    if not templates.is_dir():
        problems.append(f"模板目录没打进去：{TEMPLATE_SUBDIR}（界面会全线 500）")
    elif not list(templates.glob("*.html")):
        problems.append(f"模板目录是空的：{TEMPLATE_SUBDIR}")

    size_mb = _dir_size(dist_dir) / 1024 / 1024
    if size_mb > SIZE_BUDGET_MB:
        problems.append(f"体积 {size_mb:.0f}MB 超出预算 {SIZE_BUDGET_MB}MB")

    return problems


def _make_zip(dist_dir: Path) -> Path:
    """打 zip：解压出一个 `hunter1/` 目录，即所谓的「解压即用」。"""
    archive = DIST / f"{APP_NAME}-{sys.platform}.zip"
    print(f"== 打包 {archive.name} ==")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in sorted(dist_dir.rglob("*")):
            if item.is_file():
                bundle.write(item, item.relative_to(dist_dir.parent))
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build.py", description="构建分发产物")
    parser.add_argument("--zip", action="store_true", help="构建后再打一个 zip")
    args = parser.parse_args(argv)

    _require_pyinstaller()
    _run_pyinstaller()

    dist_dir = DIST / APP_NAME
    if not dist_dir.is_dir():
        raise SystemExit(f"没有产出 {dist_dir}")

    print("\n== 校验 ==")
    problems = verify(dist_dir)
    size_mb = _dir_size(dist_dir) / 1024 / 1024
    print(f"  目录：{dist_dir}")
    print(f"  体积：{size_mb:.1f}MB（预算 {SIZE_BUDGET_MB}MB）")
    print(f"  模板：{(dist_dir / TEMPLATE_SUBDIR).is_dir()}")

    if problems:
        print("\n产物不合格：")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    if args.zip:
        archive = _make_zip(dist_dir)
        print(f"  zip：{archive}（{archive.stat().st_size / 1024 / 1024:.1f}MB）")

    print("\n通过。分发前建议手动验一遍（见 docs/DEVELOPMENT.md 的打包小节）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
