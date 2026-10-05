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
# 模板打进去的目标目录随 PyInstaller 大版本变过：6.x 放进 `_internal/`，
# 5.x 及更早是平铺。两种都认，免得在 5.x 上把「模板在」误报成「不在」。
TEMPLATE_SUBDIRS = (
    Path("_internal") / "hunter1" / "web" / "templates",
    Path("hunter1") / "web" / "templates",
)

# 规划 §3.4 给的分发体积上限
SIZE_BUDGET_MB = 200

# 冒烟运行产物的超时：打包产物冷启动要解开归档，比源码运行慢
SMOKE_TIMEOUT_SECONDS = 90


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


def find_templates(dist_dir: Path) -> Path | None:
    """在产物里找模板目录（兼容 PyInstaller 5.x 与 6.x 两种布局）。"""
    for subdir in TEMPLATE_SUBDIRS:
        candidate = dist_dir / subdir
        if candidate.is_dir():
            return candidate
    return None


def layout_problems(dist_dir: Path) -> list[str]:
    """只看产物**长得对不对**，不执行它。返回问题列表（空 = 合格）。"""
    problems: list[str] = []

    exe = dist_dir / _exe_name()
    if not exe.is_file():
        problems.append(f"缺少可执行文件：{exe.name}")

    templates = find_templates(dist_dir)
    if templates is None:
        problems.append(
            f"模板目录没打进去（找过 {[str(p) for p in TEMPLATE_SUBDIRS]}）——界面会全线 500"
        )
    elif not list(templates.glob("*.html")):
        problems.append(f"模板目录是空的：{templates}")

    size_mb = _dir_size(dist_dir) / 1024 / 1024
    if size_mb > SIZE_BUDGET_MB:
        problems.append(f"体积 {size_mb:.0f}MB 超出预算 {SIZE_BUDGET_MB}MB")

    return problems


def looks_like_help(stdout: str) -> bool:
    """判断 `--help` 的输出是不是我们这个程序的帮助信息。

    单独抽出来是为了能直接单测这条判据 —— 否则要造一个「能跑、退出码 0、
    但什么都不输出」的可执行文件才能覆盖到。
    """
    text = stdout.lower()
    return "hunter1" in text or "usage" in text


def smoke_run(exe: Path) -> str | None:
    """真跑一次产物（`--help`）。合格返回 None，否则返回问题描述。

    存在的理由：文件在、体积对、模板在，**都不代表它能跑**。缺 DLL、
    C 扩展没打进去、import 链断裂 —— 这些只有执行起来才知道，而且要等到
    用户双击的那一刻才暴露。跑一次 `--help` 是最便宜的兜底。
    """
    try:
        result = subprocess.run(
            [str(exe), "--help"],
            capture_output=True,
            timeout=SMOKE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"产物运行超时（>{SMOKE_TIMEOUT_SECONDS}s）：{exe.name}"
    except OSError as exc:
        return f"产物无法执行：{exe.name}（{exc}）"

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()[:400]
        return f"产物运行失败（exit {result.returncode}）：{stderr}"

    if not looks_like_help(result.stdout.decode("utf-8", "replace")):
        return "产物能跑但没打印出预期的帮助信息 —— 可能入口不对"
    return None


def verify(dist_dir: Path) -> list[str]:
    """完整门禁：先看布局，布局没问题再真跑一次。"""
    problems = layout_problems(dist_dir)
    if problems:
        # 布局都不对就没必要跑 —— 免得把「缺文件」误报成「跑不起来」
        return problems

    exe = dist_dir / _exe_name()
    smoke_problem = smoke_run(exe)
    if smoke_problem is not None:
        problems.append(smoke_problem)
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
    templates = find_templates(dist_dir)
    print(f"  目录：{dist_dir}")
    print(f"  体积：{size_mb:.1f}MB（预算 {SIZE_BUDGET_MB}MB）")
    print(f"  模板：{templates if templates is not None else '缺失'}")
    print(f"  冒烟：{'通过' if not problems else '未通过'}")

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
