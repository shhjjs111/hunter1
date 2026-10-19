"""构建可分发产物（PyInstaller）。

    ./.tools/python/python.exe scripts/build.py          # 构建 + 校验
    ./.tools/python/python.exe scripts/build.py --zip    # 再打一个 zip

**构建之后一定要校验**：exe 存在不代表能跑。最常见的事故是前端产物没打进去 ——
产物能启动、`--help` 也正常，但根路径只会落进「前端产物未构建」的提示
（或 SPA 外壳加载不出来）。所以门禁分四层，逐层变贵、也能各自抓住不同的问题：

1. 静态看布局，并**写入 `VERSION`**（产物自带版本，发布链靠它核对「这份二进制是不是
   这个源码版本构建的」）；
2. 跑 `--help`（能不能起来）；
3. 跑 `--version` 比对源码版本（起来的是不是**这一版**—— 旧产物不认这个参数，会红）；
4. **真起一次服务请求各页面**，再看前端入口脚本能不能取回。

`--zip` 还会把压缩包打开验一遍（单一根目录、无越界条目、条目集与目录逐一对应、CRC、
版本一致）—— 目录合格不代表包合格，而用户拿到的恰恰是包。任一层不合格就非零退出，
别让一个坏包流出去。

不做的事：不签名、不上传。分发渠道是另一回事（见 DEVELOPMENT-PLAN §6.4）。
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import os
import re
import shutil
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

# 产物命名取自 scripts/artifact.py（发布链的唯一来源，`release.sh` 与
# `gh_publish.py` 共用它）—— 本文件不再自持一份字面量。按文件位置加载而不靠
# sys.path：测试是以「按路径加载 build.py」的方式导入本模块的，那时 scripts/
# 不在 sys.path 上（直接 `python scripts/build.py` 才在）。
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from artifact import (  # noqa: E402
    APP_NAME,
    DIST_DIR_NAME,
    artifact_name,
    exe_name,
)

# 版本号的**唯一来源**是包本身（`backend/src/hunter1/__init__.py`，见 pyproject 的
# 注释与 tests/test_version.py）。脚本在仓库根、包在 backend/src —— 显式加路径，
# 不依赖安装状态（与 make_manifest.py 同一手法）。
sys.path.insert(0, str(BACKEND / "src"))
from hunter1 import __version__ as source_version  # noqa: E402
from hunter1.platform.console import enable_utf8_console  # noqa: E402

# 本脚本打印大量中文进度（`== 构建前端 ==` 等）——在非中文 locale 的控制台上
# （英文 Windows / GitHub 的 windows runner = cp1252）会直接抛 UnicodeEncodeError。
# 与 cli / examples 共用同一份防护（见 hunter1.platform.console）。
enable_utf8_console()

#: 写进产物（并被压进 zip）的版本文件。发布链靠它核对「这份产物是不是这个源码版本
#: 构建的」—— 没有它，用一份旧 `dist/` 配当前源码生成清单不会报任何错，而用户的
#: 更新判断会长期失准（看起来有新版，实际装回旧版）。
VERSION_FILE_NAME = "VERSION"
#: 分发时随包附带的使用说明与「手机访问」启动器。
GUIDE_SOURCE = ROOT / "docs" / "使用说明.md"
GUIDE_NAME = "使用说明.md"
LAN_LAUNCHER_NAME = "hunter1-lan.cmd"
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
    ("/", 'id="root"'),  # SPA 挂载点：产物里必须有
    # 前端**深层路由**：必须回落到 index.html。这是打包最常见的翻车点 ——
    # 静态资源中间件没配 fallback 时，直接请求 /settings 之类会 404，
    # 而入口 / 与 API 全绿（docs/DEVELOPMENT.md 的打包小节列出了这 5 条）。
    ("/settings", 'id="root"'),
    ("/assistant", 'id="root"'),
    ("/crawl", 'id="root"'),
    ("/applications", 'id="root"'),
    ("/api/jobs", '"items"'),  # API 有响应且是预期的 JSON 形状
    ("/api/crawl/status", '"running"'),
    ("/api/settings", ""),  # 配置端点可达（未配置时返回 null）
)

# 前端静态资源的可达性单独断言：**先请求 index.html，取出它引用的资源路径再请求**。
# 不直接请求 `/assets/`：目录路径没有索引文件，StaticFiles 返回 404 是正确行为，
# 拿它当「资源不可达」的证据会误报（这个坑是实测踩到的）。
_SCRIPT_SRC = re.compile(r'<script[^>]+src="([^"]+)"')


def _exe_name() -> str:
    """产物可执行文件名（命名规则见 `scripts/artifact.py`）。"""
    return exe_name()


def _prefer_real_node_dir() -> None:
    """把**真 node** 的目录提到 PATH 最前 —— 与 `scripts/check.sh` 同一道防线。

    为什么需要：`npm.cmd` 在 Windows 上内部经 cmd.exe 调 `node`，而 cmd 按 PATH
    解析 `node`。若 PATH 里存在别的「node 包装」（如某工具链附带的一份运行时
    `<工具链目录>\\node.cmd`），cmd 会命中它，而那个包装
    只有一行 `"%~dp0<某运行时>.exe" %*` —— 经 PATH 调用时 `%~dp0` 展开为空、
    退化成相对名，于是报 `'"<cwd>\\<某运行时>.exe"' 不是内部或外部命令`：
        前端构建失败（exit 1）

    实测（本机、本次）：`shutil.which("node")` → `...\\node.CMD`（PATHEXT 里 .CMD
    先于 .EXE），真 node 在 `<nodejs 安装目录>\\node.exe`；`build.py` 此前不前置真 node
    目录，**打包必挂**。`check.sh` 早已前置（bash 的 `command -v node` 命中真
    node，顺带把它的目录提到 PATH 最前，cmd 于是也能解析到同一份）——同一模式
    在两处，此前只修了 check.sh 这一处。

    「找真 node 的目录」用 `shutil.which("node.exe")`：显式带 `.exe` 跳过 PATHEXT
    里的 .CMD，拿到的是**可执行**的那份（本机 `<nodejs 安装目录>\\node.exe`），其目录
    同时含 `npm.cmd`。找不到就原样返回（Linux/CI 上 PATH 本就正确，无副作用）。
    """
    node_exe = shutil.which("node.exe")
    if not node_exe:
        return
    node_dir = os.path.dirname(node_exe)
    if not node_dir:
        return
    # **无条件提到最前**，并去掉 PATH 里原有的同一项 —— 判据不能是「不在 PATH 里才加」：
    # 真 node 目录本就在 PATH 中（只是排在包装器目录之后），那种写法会跳过前置，
    # 包装器仍排在真 node 之前、问题原样复现（实测踩到）。
    current = os.environ.get("PATH", "")
    parts = [entry for entry in current.split(os.pathsep) if entry and entry != node_dir]
    os.environ["PATH"] = os.pathsep.join([node_dir, *parts])


def _ensure_frontend_built() -> None:
    """确保前端产物存在（构建前先 build 一次）。

    产物缺失时**明确失败**而不是继续：打一个没有界面的包出来，用户解开
    只会看到一段裸 API —— 那比直接报错的排查成本高得多。
    """
    dist = ROOT / "frontend" / "dist"
    frontend_dir = ROOT / "frontend"
    if not (frontend_dir / "package.json").is_file():
        raise SystemExit(f"缺少前端工程：{frontend_dir}")

    # 先把真 node 目录前置，npm.cmd 内部的 `node` 才解析到真解释器（见上面函数）。
    _prefer_real_node_dir()

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


def write_dist_extras(dist_dir: Path) -> None:
    """把给最终用户看的东西放进产物目录（于是也会进 zip）。

    非技术用户不会去 GitHub 读 README；解压后第一眼看到的就是这些文件。
    启动器必须用 CRLF —— Windows 的 cmd.exe 对纯 LF 的批处理容错很差。
    """
    if not GUIDE_SOURCE.is_file():
        raise SystemExit(f"缺少使用说明：{GUIDE_SOURCE}")
    (dist_dir / GUIDE_NAME).write_text(
        GUIDE_SOURCE.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )
    (dist_dir / LAN_LAUNCHER_NAME).write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        'cd /d "%~dp0"\r\n'
        "echo Starting Hunter1 for phone access...\r\n"
        "echo Keep this window open while using your phone.\r\n"
        "echo.\r\n"
        "hunter1.exe serve --lan\r\n"
        "echo.\r\n"
        "echo Hunter1 has stopped. Press any key to close.\r\n"
        "pause >nul\r\n",
        encoding="utf-8",
        newline="\r\n",
    )


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

    **但抓不到前端产物类事故**：`--help` 走 argparse，早于静态资源挂载。
    前端产物缺失或路径错位时它照样 exit 0，而实际起服务后 `/` 只会给
    「前端产物未构建」的 503。那一层归 `smoke_serve()`。
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


def _local_client(base_url: str, *, timeout: float, follow_redirects: bool = True) -> httpx.Client:
    """构造请求**本地**产物的 client —— 必须禁用环境代理（`trust_env=False`）。

    实测（探针，对照组）：开着系统代理时，httpx 默认 `trust_env=True` 会调
    `urllib.request.getproxies()`（该函数在 Windows 上**读注册表**），于是连
    `127.0.0.1` 的请求也被送给代理，拿到 **502**；同一请求改成
    `trust_env=False` 得到 **200**。后果是冒烟门禁报「产物不完整」，而产物其实
    是好的 —— 报错把人指向完全错误的方向。本地请求就该直连。
    """
    return httpx.Client(
        base_url=base_url,
        timeout=timeout,
        follow_redirects=follow_redirects,
        trust_env=False,
    )


def check_frontend_assets(
    base_url: str, *, timeout: float = SMOKE_PAGE_TIMEOUT_SECONDS
) -> list[str]:
    """从 index.html 里取出入口脚本路径并请求它。

    这是「前端产物真的能被浏览器加载」的最强静态证据：HTML 能取回、
    它引用的脚本能被取回。JS 执行后的渲染由前端测试覆盖（见
    frontend/src/app/App.test.tsx 的整体渲染验收）。
    """
    problems: list[str] = []
    with _local_client(base_url, timeout=timeout) as client:
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

    这是唯一能兑现「产物能跑」这句承诺的做法：文件在、体积对、前端产物目录存在，
    都不代表页面服务得出来。只有真的请求一次才知道 —— 产物路径错位时，
    目录存在、`--help` 正常、但 `/` 只会给 503。
    """
    problems: list[str] = []
    with _local_client(base_url, timeout=timeout) as client:
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
    最后报一句含糊的「未能就绪」，把真正的线索（500 / 前端产物缺失提示）
    埋掉。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False  # 已经死了，再等没意义
        try:
            with _local_client(base_url, timeout=3.0, follow_redirects=False) as client:
                client.get("/")
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


def smoke_default_args(exe: Path, *, timeout: float = SMOKE_SERVE_TIMEOUT_SECONDS) -> str | None:
    """**不带任何参数**启动产物，判据是「它真的在那儿服务」。

    为什么必须有这一层：便携交付形态下用户拿到的就是一个 exe，默认动作是**双击**
    —— 而双击不带参数。实测（用户报告 + 本机复现）：无参数时程序打印帮助后
    `exit 1`，一个黑框一闪而过、界面从不出现。而此前所有冒烟用的都是
    `hunter1.exe serve`（显式带子命令）——**用户最常走的那条路从未被覆盖**。

    与「不覆盖的路径最先烂掉」（见 `--with-pack` 的注释）是同一条教训：
    只测「你会怎么调用它」，不测「用户会怎么打开它」。

    端口用默认 8000：无参数路径没法传 `--port`（那正是「双击」的含义）。
    若 8000 已被占用，就无法验证无参数启动这条路径。此时必须明确失败而不是
    返回 None：`smoke_run` 把 None 当通过，会制造「关键路径没查却全绿」。
    判断「起没起来」用同一个 `wait_for_http`：拿到任何 HTTP 响应即为起来了，
    **进程提前退出**则立即返回（旧行为就是立刻退，这条能稳定抓住它）。
    """
    port = 8000
    base_url = f"http://127.0.0.1:{port}"

    # 端口被别的程序占着时，「起不来」与产物无关，但这条冒烟也确实没执行。
    # 返回可行动的问题而不是 None，避免把「没查」冒充成「通过」。
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return "无参数启动冒烟未验证：127.0.0.1:8000 已被占用。请释放该端口后重新运行打包门禁。"

    with tempfile.TemporaryDirectory(prefix="hunter1-smoke-default-") as workspace:
        log_path = Path(workspace) / "default.log"
        runtime_data = Path(workspace) / "runtime-data"
        # 只给 exe 本体，**不加任何参数** —— 与双击完全一致。
        # 数据目录用环境变量显式指向临时区：打包态默认会落到 exe 旁边的
        # `dist/hunter1/data/`，而后续 zip 以 `dist/hunter1` 为源目录；若把默认库
        # 留在那里，真实配置和 API Key 就可能被一起发布。临时覆盖既模拟双击，
        # 又让冒烟不产生任何可入包的数据。
        child_env = {**os.environ, "HUNTER1_DATA_DIR": str(runtime_data)}
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [str(exe)],
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=workspace,
                env=child_env,
            )
            try:
                if not wait_for_http(base_url, process, timeout=timeout):
                    _stop(process)
                    return (
                        "无参数启动（双击 exe 的路径）未能在 "
                        f"{timeout:.0f}s 内提供服务 —— 用户会看到黑框一闪、界面不出现。"
                        f"日志尾部：{_read_tail(log_path)}"
                    )
                problems = check_pages(base_url)
            finally:
                _stop(process)

        if problems:
            tail = _read_tail(log_path, 300)
            return f"无参数启动后页面异常：{'；'.join(problems)}｜日志尾部：{tail}"
    return None


def smoke_run(exe: Path) -> str | None:
    """完整冒烟：先快后深。任一层不合格就返回问题描述。

    分几层是因为它们能抓到的问题不同，且代价差一个数量级：
    `--help` 大约零点几秒，`--version` 同样快（判「版本对不对」），起服务要几秒。
    先跑快的，能快速失败。

    `smoke_default_args` 单列一层：它验的是**用户双击**那条路（不带参数），
    与 `smoke_serve`（显式 `serve` + 临时端口）覆盖的不是同一件事 ——
    实测正是前者漏了、后者全绿。
    """
    return smoke_help(exe) or smoke_version(exe) or smoke_serve(exe) or smoke_default_args(exe)


def verify(dist_dir: Path) -> list[str]:
    """完整门禁：先看布局，布局没问题再真跑一次。"""
    problems = layout_problems(dist_dir) + version_problems(dist_dir)
    if problems:
        # 布局都不对就没必要跑 —— 免得把「缺文件」误报成「跑不起来」
        return problems

    exe = dist_dir / _exe_name()
    smoke_problem = smoke_run(exe)
    if smoke_problem is not None:
        problems.append(smoke_problem)
    return problems


def _make_zip(dist_dir: Path) -> Path:
    """打 zip 到**暂存名**（`<产物名>.tmp`）：解压出一个 `hunter1/` 目录，即「解压即用」。

    刻意不直接写正式名 —— 由 `produce_zip` 校验通过后再改名。理由见 `produce_zip`。
    """
    archive = DIST / artifact_name()
    staging = archive.with_name(archive.name + ".tmp")
    staging.unlink(missing_ok=True)
    print(f"== 打包 {archive.name} ==")
    with zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in sorted(dist_dir.rglob("*")):
            if item.is_file() and not _is_runtime_data(item, dist_dir):
                bundle.write(item, item.relative_to(dist_dir.parent))
    return staging


def _is_runtime_data(path: Path, dist_dir: Path) -> bool:
    """产物目录里的运行数据永远不进入发布包。"""
    try:
        relative = path.relative_to(dist_dir)
    except ValueError:
        return True
    return bool(relative.parts) and relative.parts[0] == "data"


def produce_zip(dist_dir: Path) -> tuple[Path | None, list[str]]:
    """打 zip 并校验：合格返回 `(正式产物路径, [])`，不合格返回 `(None, 问题列表)`。

    **官方产物路径上只可能出现校验通过的包。** 此前 `main` 直接写正式名再校验，失败时
    只 `return 1` 不删文件 —— 一份被门禁判不合格的包就留在 `dist/` 里，而 `release.sh`
    与 `gh_publish.py` 对该路径只查「文件在不在」，下一个人跳过重建直接发版就会把它
    发出去。更糟的是：manifest 的 sha256 正是这份坏包的哈希，用户端 checksum 校验
    **必然通过**，没有任何一层能发现。这里用「暂存 → 校验 → 改名」，不合格则删暂存。
    """
    final_archive = DIST / artifact_name()
    final_archive.unlink(missing_ok=True)  # 旧包不该顶替这一次的结论
    staging = _make_zip(dist_dir)
    problems = zip_problems(staging, dist_dir)
    if problems:
        staging.unlink(missing_ok=True)
        return None, problems
    staging.replace(final_archive)
    return final_archive, []


def write_version_file(dist_dir: Path) -> Path:
    """把源码版本写进产物目录（于是也进了 zip）。

    这是「产物↔源码版本」可核对的前提：没有它，一份**上次**构建的 `dist/` 配上当前
    源码照样能生成清单，而版本号写在清单里 —— 用户看到「有新版本」，装回去的却是旧
    二进制，`is_newer` 从此长期失准且不报任何错。

    行尾固定 LF（同 export_openapi.py）：产物不该由平台决定行尾。
    """
    target = dist_dir / VERSION_FILE_NAME
    target.write_text(f"{source_version}\n", encoding="utf-8", newline="\n")
    return target


def version_problems(dist_dir: Path) -> list[str]:
    """产物目录里的版本文件必须存在且与源码一致。"""
    target = dist_dir / VERSION_FILE_NAME
    if not target.is_file():
        return [f"产物缺 {VERSION_FILE_NAME}（无法核对产物↔源码版本；旧产物请重新构建）"]
    found = target.read_text(encoding="utf-8", errors="replace").strip()
    if found != source_version:
        return [f"产物版本 {found!r} 与源码 {source_version!r} 不一致 —— 这是一份旧构建"]
    return []


def version_output_problem(returncode: int, stdout: str, expected: str) -> str | None:
    """判「产物自报的版本」是否合格（纯函数，便于直接单测这条判据）。

    为什么要看产物**自己**报的版本：`VERSION` 文件是我写进去的，而用户跑的是二进制。
    二进制里的版本是编译期烧进去的字面量 —— 只有让它自己说，才能证明「跑起来的这份
    就是这个版本」。旧产物不认识 `--version`（argparse 报 unrecognized arguments、
    exit 2），这条判据正是为此设的。
    """
    text = (stdout or "").strip()
    if returncode != 0:
        return f"产物执行 `--version` 失败（exit {returncode}）：{text[-200:] or '(无输出)'}"
    if expected not in text:
        return f"产物自报的版本里没有 {expected!r}（实际：{text[:200] or '(空)'}）"
    return None


def smoke_version(exe: Path) -> str | None:
    """跑一次 `exe --version` 并核对版本。合格返回 None。"""
    try:
        result = subprocess.run(
            [str(exe), "--version"],
            capture_output=True,
            timeout=SMOKE_HELP_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"产物 `--version` 超时（>{SMOKE_HELP_TIMEOUT_SECONDS}s）：{exe.name}"
    except OSError as exc:
        return f"产物无法执行：{exe.name}（{exc}）"
    return version_output_problem(
        result.returncode, result.stdout.decode("utf-8", "replace"), source_version
    )


def _unsafe_zip_entry(name: str) -> str | None:
    """zip 条目名是否越界（解压即覆盖任意位置）。合格返回 None。"""
    normalized = name.replace("\\", "/")
    if name.endswith("/"):
        return None  # 目录条目本身无害，交给下面的前缀/条目集判据
    if normalized.startswith("/") or ".." in normalized.split("/"):
        return "绝对路径或以 .. 开头"
    if ":" in normalized.split("/")[0]:
        return "带盘符"
    return None


def zip_problems(archive: Path, dist_dir: Path) -> list[str]:
    """打开压缩包验形态 —— 让 zip 这条交付路径第一次真的被门禁看一遍。

    为什么不能只看目录：目录对了不代表压缩包对了。打包时把 `dist/` 也裹进去（解压出
    `dist/hunter1/...`）、漏掉文件、写进绝对路径或 `..`（解压覆盖任意位置），目录形态
    全都看不出来 —— 而这些恰好是「解压即用」最常见的翻车点。

    判据：
    1. CRC 自检（`testzip`）—— 传输/写入损坏；
    2. 所有条目都在 `hunter1/` 之下，且不含绝对路径 / `..` / 盘符（防路径穿越）；
    3. **条目集与产物目录逐一对应**（漏文件或有来路不明的多文件都报）；
    4. 压缩包里的 `VERSION` 与源码一致、exe 与前端产物齐备（防「包里的东西不是刚验过
       的那份」，也防 zip 用了过期目录）。
    """
    if not archive.is_file():
        return [f"压缩包不存在：{archive}"]

    problems: list[str] = []
    with zipfile.ZipFile(archive) as bundle:
        broken = bundle.testzip()
        if broken is not None:
            problems.append(f"压缩包内容损坏（CRC 校验失败）：{broken}")

        names = [info.filename for info in bundle.infolist()]
        prefix = f"{APP_NAME}/"
        for name in names:
            unsafe = _unsafe_zip_entry(name)
            if unsafe is not None:
                problems.append(f"压缩包条目不安全（{unsafe}）：{name}")
                continue
            if not name.startswith(prefix):
                problems.append(f"压缩包条目不在 {prefix} 之下：{name}（解压出来是散开的）")

        inside = {name for name in names if not name.endswith("/")}
        expected = {
            str(item.relative_to(dist_dir.parent)).replace("\\", "/")
            for item in dist_dir.rglob("*")
            if item.is_file() and not _is_runtime_data(item, dist_dir)
        }
        missing = sorted(expected - inside)
        extra = sorted(inside - expected)
        if missing:
            problems.append(f"压缩包缺 {len(missing)} 个文件（例：{missing[:3]}）")
        if extra:
            problems.append(f"压缩包多出 {len(extra)} 个文件（例：{extra[:3]}）")

        version_entry = f"{prefix}{VERSION_FILE_NAME}"
        if version_entry not in inside:
            problems.append(f"压缩包里没有 {version_entry}（无法核对产物↔源码版本）")
        else:
            found = bundle.read(version_entry).decode("utf-8", "replace").strip()
            if found != source_version:
                problems.append(f"压缩包版本 {found!r} 与源码 {source_version!r} 不一致")

        if f"{prefix}{_exe_name()}" not in inside:
            problems.append(f"压缩包里没有可执行文件：{prefix}{_exe_name()}")
        frontend = [name for name in inside if name.startswith(prefix) and "/web_dist/" in name]
        if not any(name.endswith("/web_dist/index.html") for name in frontend):
            problems.append("压缩包里没有前端产物（web_dist/index.html）——界面会打不开")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build.py", description="构建分发产物")
    parser.add_argument("--zip", action="store_true", help="构建后再打一个 zip")
    args = parser.parse_args(argv)

    _ensure_frontend_built()
    _require_pyinstaller()
    _run_pyinstaller()

    dist_dir = DIST / DIST_DIR_NAME
    if not dist_dir.is_dir():
        raise SystemExit(f"没有产出 {dist_dir}")

    # 先落版本文件再校验：布局检查里的 version_problems 与冒烟里的 `--version`
    # 都要看它，而它是「产物↔源码版本」这条承诺的载体。
    write_version_file(dist_dir)
    write_dist_extras(dist_dir)

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
        # 压缩包**自己**也要过一遍：目录合格不代表包合格（少了文件、裹成两级目录、
        # 写进绝对路径，目录里都看不出来）。这是 zip 第一次被门禁真的打开。
        # `produce_zip` 只在**校验通过**时才把包落到正式名 —— 不合格则 `dist/`
        # 里不存在该 zip（见它的 docstring）。
        archive, zip_problems_found = produce_zip(dist_dir)
        if archive is None:
            print("\n压缩包不合格：")
            for problem in zip_problems_found:
                print(f"  - {problem}")
            return 1
        print(f"  zip：{archive}（{archive.stat().st_size / 1024 / 1024:.1f}MB）")
        print(f"  zip 校验：通过（{len(zipfile.ZipFile(archive).namelist())} 个条目）")

    print("\n通过。分发前建议手动验一遍（见 docs/DEVELOPMENT.md 的打包小节）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
