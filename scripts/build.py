"""构建可分发产物（PyInstaller）。

    ./.tools/python/python.exe scripts/build.py          # 构建 + 校验
    ./.tools/python/python.exe scripts/build.py --zip    # 再打一个 zip

**构建之后一定要校验**：exe 存在不代表能跑。最常见的事故是模板没打进去 ——
产物能启动、`--help` 也正常，但每个页面都 500。所以门禁分两层：静态看布局，
再**真起一次服务请求各页面**。任一层不合格就非零退出，别让一个坏包流出去。

不做的事：不签名、不上传。分发渠道是另一回事（见 DEVELOPMENT-PLAN §6.4）。
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
DIST = ROOT / "dist"
APP_NAME = "hunter1"
# 前端产物打进去的目标目录随 PyInstaller 大版本变过：6.x 放进 `_internal/`，
# 5.x 及更早是平铺。两种都认，免得在 5.x 上把「产物在」误报成「不在」。
# （Wave 6 之前这里找的是 web/templates；SSR 层删除后换成前端构建产物。）
FRONTEND_SUBDIRS = (
    Path("_internal") / "hunter1" / "web_dist",
    Path("hunter1") / "web_dist",
)

# 体积硬上限。实测约 42MB，给它约 2x 余量：
# 原先写 200MB —— 那不是门禁，是摆设（实际值的近 5 倍，永远不会触发）。
# 收到 80MB 才有区分度：真把不该带的东西打进去了（多带一个运行时、
# 依赖膨胀、误打包数据目录），它会红。
SIZE_BUDGET_MB = 80

# 冒烟超时。产物冷启动要解归档，比源码运行慢，所以给得比直觉宽。
SMOKE_HELP_TIMEOUT_SECONDS = 90
SMOKE_SERVE_TIMEOUT_SECONDS = 90
SMOKE_PAGE_TIMEOUT_SECONDS = 20.0

# 冒烟探针：(路径, 断言说明)。前端现在是 SPA —— 页面内容由 JS 渲染，
# 静态 HTML 里只有挂载点与资源引用，所以断言换成「**结构性事实**」：
# SPA 外壳能返回、入口脚本被引用、API 有响应。
#
# 为什么不再断言中文文案：那需要执行 JS（headless 浏览器）。本层冒烟
# 的职责是「产物能不能起、前端与 API 两条路通不通」，渲染正确性由
# 前端的组件测试与整体渲染验收覆盖（见 frontend/src/app/App.test.tsx）。
SMOKE_PAGES: tuple[tuple[str, str], ...] = (
    ("/", 'id="root"'),          # SPA 挂载点：产物里必须有
    ("/api/jobs", '"items"'),     # API 有响应且是预期的 JSON 形状
    ("/api/crawl/status", '"running"'),
    ("/api/settings", ""),        # 配置端点可达（未配置时返回 null）
)

# 前端静态资源的可达性单独断言：**先请求 index.html，取出它引用的资源路径再请求**。
# 不直接请求 `/assets/`：目录路径没有索引文件，StaticFiles 返回 404 是正确行为，
# 拿它当「资源不可达」的证据会误报（这个坑是实测踩到的）。
_SCRIPT_SRC = re.compile(r'<script[^>]+src="([^"]+)"')


def _exe_name() -> str:
    return f"{APP_NAME}.exe" if os.name == "nt" else APP_NAME


def _ensure_frontend_built() -> None:
    """确保前端产物存在（构建前先 build 一次）。

    产物缺失时**明确失败**而不是继续：打一个没有界面的包出来，用户解开
    只会看到一段裸 API —— 那比直接报错的排查成本高得多。
    """
    dist = ROOT / "frontend" / "dist"
    frontend_dir = ROOT / "frontend"
    if not (frontend_dir / "package.json").is_file():
        raise SystemExit(f"缺少前端工程：{frontend_dir}")

    npm = "npm.cmd" if os.name == "nt" else "npm"
    print("== 构建前端 ==")
    result = subprocess.run([npm, "run", "build"], cwd=frontend_dir, check=False, shell=False)
    if result.returncode != 0:
        raise SystemExit(f"前端构建失败（exit {result.returncode}）")
    if not (dist / "index.html").is_file():
        raise SystemExit(f"前端构建未产出 index.html：{dist}")


def _require_pyinstaller() -> None:
    if importlib.util.find_spec("PyInstaller") is None:
        raise SystemExit(
            "缺少 PyInstaller。先装：\n"
            f'  {sys.executable} -m pip install -e ".[build]"\n'
            "（或直接：pip install --index-url https://pypi.org/simple pyinstaller）"
        )


def _run_pyinstaller() -> None:
    # --clean 清掉 PyInstaller 的缓存目录，避免拿到上一次的残留。
    # cwd=backend：spec 里的相对路径（src/...）以工程根为基准解析；
    # --distpath / --workpath 指到仓库根 —— 分发产物是跨端整合物，就该放在根，
    # backend/ 里不留构建残渣。
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--log-level",
        "WARN",
        "--distpath",
        str(DIST),
        "--workpath",
        str(ROOT / "build"),
        "hunter1.spec",
    ]
    print("== 构建 ==")
    result = subprocess.run(command, cwd=BACKEND, check=False)
    if result.returncode != 0:
        raise SystemExit(f"PyInstaller 失败（exit {result.returncode}）")


def _dir_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def find_frontend_dist(dist_dir: Path) -> Path | None:
    """在产物里找前端产物目录（兼容 PyInstaller 5.x 与 6.x 两种布局）。"""
    for subdir in FRONTEND_SUBDIRS:
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

    frontend = find_frontend_dist(dist_dir)
    if frontend is None:
        problems.append(
            f"前端产物没打进去（找过 {[str(p) for p in FRONTEND_SUBDIRS]}）"
            "——界面会打不开（只剩裸 API）"
        )
    elif not (frontend / "index.html").is_file():
        problems.append(f"前端产物缺 index.html：{frontend}")
    elif not (frontend / "assets").is_dir():
        problems.append(f"前端产物缺 assets 目录：{frontend / 'assets'}（界面会加载不出脚本）")

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


def smoke_help(exe: Path) -> str | None:
    """最快的一层：跑一次 `--help`。合格返回 None。

    能抓到「根本起不来」这类问题（缺 DLL、C 扩展没打进去、import 链断裂）——
    实测：把 exe 单独拿出来（缺 `_internal/`）会 exit 127 并报
    `Failed to load Python DLL`。

    **但抓不到模板类事故**：`--help` 走 argparse，早于模板加载。模板缺失或
    路径错位时它照样 exit 0，而实际起服务后每个页面都 500。那一层归
    `smoke_serve()`。
    """
    try:
        result = subprocess.run(
            [str(exe), "--help"],
            capture_output=True,
            timeout=SMOKE_HELP_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"产物运行超时（>{SMOKE_HELP_TIMEOUT_SECONDS}s）：{exe.name}"
    except OSError as exc:
        return f"产物无法执行：{exe.name}（{exc}）"

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()[:400]
        return f"产物运行失败（exit {result.returncode}）：{stderr}"

    if not looks_like_help(result.stdout.decode("utf-8", "replace")):
        return "产物能跑但没打印出预期的帮助信息 —— 可能入口不对"
    return None


def free_port() -> int:
    """要一个（此刻）空闲的本地端口。

    绑 0 让内核分配、拿到号之后立刻释放 —— 中间有一个极小的窗口会被别人抢走。
    可以接受：真被抢了会表现为「启动超时」，报错信息也指向同一条排查路径。
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def check_frontend_assets(base_url: str, *, timeout: float = SMOKE_PAGE_TIMEOUT_SECONDS) -> list[str]:
    """从 index.html 里取出入口脚本路径并请求它。

    这是「前端产物真的能被浏览器加载」的最强静态证据：HTML 能取回、
    它引用的脚本能被取回。JS 执行后的渲染由前端测试覆盖（见
    frontend/src/app/App.test.tsx 的整体渲染验收）。
    """
    problems: list[str] = []
    with httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=True) as client:
        try:
            index = client.get("/")
        except httpx.HTTPError as exc:
            return [f"/ 请求失败：{type(exc).__name__}"]
        sources = _SCRIPT_SRC.findall(index.text)
        if not sources:
            return ["index.html 里找不到 <script src=...> —— 前端产物不完整"]
        for src in sources:
            try:
                response = client.get(src)
            except httpx.HTTPError as exc:
                problems.append(f"{src} 请求失败：{type(exc).__name__}")
                continue
            if response.status_code != 200:
                problems.append(f"{src} 返回 {response.status_code}（入口脚本加载不了）")
    return problems


def check_pages(base_url: str, *, timeout: float = SMOKE_PAGE_TIMEOUT_SECONDS) -> list[str]:
    """请求每个页面，返回问题列表（空 = 全好）。

    这是唯一能兑现「产物能跑」这句承诺的做法：文件在、体积对、模板目录存在，
    都不代表页面渲染得出来。只有真的请求一次才知道 —— 模板路径错位时，
    目录存在、`--help` 正常、但每个页面 500。
    """
    problems: list[str] = []
    with httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=True) as client:
        for path, needle in SMOKE_PAGES:
            try:
                response = client.get(path)
            except httpx.HTTPError as exc:
                problems.append(f"{path} 请求失败：{type(exc).__name__}")
                continue
            if response.status_code != 200:
                problems.append(f"{path} 返回 {response.status_code}（期望 200）")
                continue
            if needle and needle not in response.text:
                problems.append(f"{path} 内容不含「{needle}」——产物可能不完整")
    return problems


def wait_for_http(base_url: str, process: subprocess.Popen[bytes], *, timeout: float) -> bool:
    """等产物把服务起起来。进程提前退出就立即返回 False（别干等超时）。

    判据是「**拿到了任何 HTTP 响应**」，而不是「返回 200」。
    收到 500 同样说明服务已经就绪 —— 只是内容有问题，那正是 `check_pages`
    要报的事。若把 200 当就绪判据，一个「页面全 500」的产物会一直等到超时，
    最后报一句含糊的「未能就绪」，把真正的线索（500 / TemplateNotFound）
    埋掉。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False  # 已经死了，再等没意义
        try:
            with httpx.Client(timeout=3.0) as client:
                client.get(f"{base_url}/")
            return True  # 有响应（哪怕是 500）＝ 服务起来了
        except httpx.HTTPError:
            pass
        time.sleep(0.4)
    return False


def _stop(process: subprocess.Popen[bytes]) -> None:
    """收掉冒烟进程。先请它退（给 10s 收尾），不行再强杀。"""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _read_tail(path: Path, limit: int = 600) -> str:
    """读日志尾部，用于报错时给人看真正的线索。"""
    with contextlib.suppress(OSError):
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        return text[-limit:] if text else "(无输出)"
    return "(读不到日志)"


def smoke_serve(exe: Path, *, timeout: float = SMOKE_SERVE_TIMEOUT_SECONDS) -> str | None:
    """最深的一层：真起一次服务、请求各页面。合格返回 None。

    用临时库、临时端口，不碰用户数据、不占固定端口。跑完一定收进程 ——
    哪怕中途返回。

    日志**写文件而不是管道**：产物出错时会刷大量 traceback，管道缓冲区写满后
    子进程会阻塞在写日志上、不再响应请求，表现为诡异的 ReadTimeout。
    （这个坑只有在「真跑一次」时才暴露，也正是服务冒烟的价值所在。）
    """
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"

    with tempfile.TemporaryDirectory(prefix="hunter1-smoke-") as workspace:
        workspace_path = Path(workspace)
        db_path = workspace_path / "smoke.db"
        log_path = workspace_path / "serve.log"
        command = [
            str(exe),
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-browser",
            "--db",
            str(db_path),
        ]
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            try:
                if not wait_for_http(base_url, process, timeout=timeout):
                    _stop(process)
                    return (
                        f"产物起服务后 {timeout:.0f}s 内未能就绪；日志尾部：{_read_tail(log_path)}"
                    )
                problems = check_pages(base_url) + check_frontend_assets(base_url)
            finally:
                _stop(process)

        if problems:
            return f"{'；'.join(problems)}｜日志尾部：{_read_tail(log_path, 300)}"
    return None


def smoke_run(exe: Path) -> str | None:
    """完整冒烟：先快后深。任一层不合格就返回问题描述。

    分两层是因为它们能抓到的问题不同，且代价差一个数量级：
    `--help` 大约零点几秒，起服务要几秒。先跑快的，能快速失败。
    """
    return smoke_help(exe) or smoke_serve(exe)


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

    _ensure_frontend_built()
    _require_pyinstaller()
    _run_pyinstaller()

    dist_dir = DIST / APP_NAME
    if not dist_dir.is_dir():
        raise SystemExit(f"没有产出 {dist_dir}")

    print("\n== 校验 ==")
    problems = verify(dist_dir)
    size_mb = _dir_size(dist_dir) / 1024 / 1024
    frontend = find_frontend_dist(dist_dir)
    print(f"  目录：{dist_dir}")
    print(f"  体积：{size_mb:.1f}MB（预算 {SIZE_BUDGET_MB}MB）")
    print(f"  前端产物：{frontend if frontend is not None else '缺失'}")
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
