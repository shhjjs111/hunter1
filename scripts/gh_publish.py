#!/usr/bin/env python
"""把 v<版本> 推到 GitHub 并建 Release —— 发布流程的「远端」那一步。

    ./.tools/python/python.exe scripts/gh_publish.py <owner/repo> [--create-repo] [--dry-run]

设计要点（每条都有来由，别随手改）：

1. **令牌只从文件/环境读，绝不进 argv**。令牌出现在命令行参数里，同机其它进程
   在进程列表里就能看到（Windows 上尤其容易）。本脚本只从 `GITHUB_TOKEN` 环境
   变量或 `~/.hunter1_gh_token` 读。
2. **git 远端地址在脚本文件里构造**。本项目环境开着 `/mirror china` 时，会改写
   「命令文本」里的 github.com —— 实测 `git config --local x "https://github.com/a/b"`
   存进去的是 `gitcode.com/gh_mirror/a/b`（只读镜像，push 必失败）。而**脚本文件
   内部**的字符串不受影响，所以地址写在这里是安全的；若哪天改成从命令行传，就会
   重新踩上这个坑。
3. **推送用 GIT_ASKPASS 供凭据**，不把令牌拼进 URL —— 后者会写进 .git/config 或
   留在进程列表。
4. **Release 附件里的 manifest 必须用真实 URL 重新生成**：url 是绝对地址，上传前
   无从得知；忘了替换就是把一份指向占位符的清单永久挂在 Release 上。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 显式加路径而不依赖安装状态：本机解释器是 Python embeddable，不走 PYTHONPATH，
# 所以下面的 import 必须晚于这一行（不是「忘了放顶部」）。
sys.path.insert(0, str(ROOT / "backend" / "src"))

from hunter1 import __version__  # noqa: E402
from hunter1.platform.update.rules import ReleaseManifest  # noqa: E402

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
DEFAULT_TOKEN_FILE = Path.home() / ".hunter1_gh_token"


def _enable_utf8_output() -> None:
    """让 Windows 控制台也能正确打印 ✓ / ↑ 这类字符。

    实测：默认代码页（简中 Windows 是 GBK）下，`print("✓ …")` 直接抛
    `UnicodeEncodeError: 'gbk' codec can't encode character '\\u2713'` ——
    整个发布流程在第一行输出就崩。控制台代码页与流编码都调到 UTF-8。

    任何一步失败都静默跳过：显示不好是小事，不能因此让脚本起不来。
    与 `hunter1.cli` 的 `_enable_utf8_console` 同一套做法。
    """
    if os.name == "nt":
        with contextlib.suppress(Exception):
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


class PublishError(RuntimeError):
    pass


def resolve_target(owner_repo: str, owner: str, repo: str) -> tuple[str, str]:
    """决定往哪个仓库发。

    `owner_repo` 给了就用它（`owner/repo` 形式）；否则用 `owner` + `repo`。
    返回的 owner 可能为空串 —— 表示「稍后用令牌账号补上」，这是双击入口的
    默认路径（用户不必事先知道自己的账号名）。
    """
    if owner_repo:
        if "/" not in owner_repo:
            raise PublishError("owner/repo 格式不对（应形如 acme/hunter1）")
        head, tail = owner_repo.split("/", 1)
        return head.strip(), tail.strip()
    return owner.strip(), repo.strip()


def verify_token(token: str) -> str | None:
    """令牌有效时返回账号名，否则 None。"""
    try:
        return api_request("GET", "/user", token).get("login") or None
    except PublishError:
        return None


def save_token(token: str) -> Path:
    """存到仓库外的文件。

    Windows 下不靠权限位（`chmod` 在 NTFS 上基本无效），而是靠**用户主目录本身
    的 ACL** —— 默认只有本账号能读 `C:\\Users\\<你>`。先写临时文件再替换，
    避免中断留下半截令牌。
    """
    path = DEFAULT_TOKEN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(token, encoding="utf-8")
    tmp.replace(path)
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path


def prompt_for_token() -> str:
    """交互式要令牌：不回显、当场校验、通过才落盘。

    这是双击入口的主要路径（`release.cmd` → 本脚本）。用 `getpass` 而非
    `input`：令牌不回显，旁人看不到屏幕；也不进 shell 历史（这是双击运行，
    根本没有 shell）。
    """
    import getpass

    print("需要 GitHub 令牌 —— 输入一次，之后会自动复用。")
    print("  生成：https://github.com/settings/tokens")
    print("  勾选：repo（classic）；或 Contents: Read and write（fine-grained）")
    print()
    # 空回车**不**计入尝试次数：手滑敲了三次回车就被锁在外面，是很糟的体验。
    # 只有「令牌被 GitHub 拒绝」才算一次失败。
    attempts = 0
    while attempts < 3:
        try:
            token = getpass.getpass("粘贴令牌后回车（输入不回显）：").strip()
        except EOFError:
            # 没有可读的输入（stdin 被重定向/关闭）。不接的话这里会吐一整段
            # traceback —— 对一个「双击就能用」的工具来说是最差的失败姿态。
            raise PublishError(
                "读取输入被中断（stdin 不是终端）。请在终端里运行，"
                "或改用 GITHUB_TOKEN 环境变量 / scripts/gh_setup.sh。"
            ) from None
        if not token:
            print("  没读到内容，再试一次。")
            continue
        for prefix in ("GITHUB_TOKEN=", "github_token="):
            if token.startswith(prefix):
                token = token[len(prefix) :]
                print("  （已去掉开头的 GITHUB_TOKEN= 前缀）")
                break
        # 粘贴常带尾随空格/换行；令牌内部不含空白。
        token = "".join(token.split())
        login = verify_token(token)
        if login is None:
            attempts += 1
            print(f"  ✗ GitHub 拒绝了它（还剩 {3 - attempts} 次）。", file=sys.stderr)
            print("    常见原因：已过期 / 已被撤销 / 复制时漏了字符。", file=sys.stderr)
            continue
        path = save_token(token)
        print(f"  ✓ 令牌有效，账号：{login}")
        print(f"  ✓ 已存到 {path}（仓库外，不会被提交）")
        return token
    raise PublishError("连续 3 次都没通过校验 —— 先到 GitHub 确认令牌是否有效。")


def read_token(*, interactive: bool = True) -> str:
    """取令牌：环境变量 → 令牌文件 → （交互式）当场输入。

    交互询问只在**真的连着终端**时发生（`isatty`）。CI 里没有终端，所以不会
    卡在一个永远等不到输入的提示上 —— 而是明确报错，让人去配 `GITHUB_TOKEN`。
    """
    env = os.environ.get("GITHUB_TOKEN", "").strip()
    if env:
        return env
    if DEFAULT_TOKEN_FILE.is_file():
        token = DEFAULT_TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    if interactive and sys.stdin.isatty():
        return prompt_for_token()
    raise PublishError(
        "没有找到令牌，且当前不是交互式终端（无法提示输入）。"
        "请设 GITHUB_TOKEN 环境变量，或先跑一次 `bash scripts/gh_setup.sh`。"
    )


def api_request(
    method: str,
    path: str,
    token: str,
    *,
    payload: dict | None = None,
    raw: bytes | None = None,
    content_type: str = "application/json",
    expect: tuple[int, ...] = (200, 201, 204),
) -> dict:
    """打一次 GitHub API。失败时把状态码与响应体一起抛出来（便于定位）。"""
    url = path if path.startswith("http") else f"{API}{path}"
    data = (
        raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    )
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
            if response.status not in expect:
                raise PublishError(f"{method} {path} → HTTP {response.status}")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise PublishError(f"{method} {path} → HTTP {exc.code}：{detail}") from None
    except urllib.error.URLError as exc:
        raise PublishError(f"{method} {path} → 网络不可达：{exc.reason}") from None


def git(*args: str, token: str, cwd: Path = ROOT) -> str:
    """跑一条 git 命令，凭据经 GIT_ASKPASS 临时脚本提供（不落盘、不进 argv）。

    令牌写成 `x-access-token` 的用户名 + 令牌作密码 —— GitHub 对 PAT 的约定。
    """
    askpass = Path(os.environ.get("TEMP", "/tmp")) / f".gh_askpass_{os.getpid()}.sh"
    try:
        askpass.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            "  *[Uu]sername*) printf '%s' 'x-access-token' ;;\n"
            '  *) printf %s "$HUNTER1_GH_TOKEN" ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        askpass.chmod(0o700)
        env = dict(os.environ, GIT_ASKPASS=str(askpass), HUNTER1_GH_TOKEN=token)
        env.pop("GIT_TERMINAL_PROMPT", None)
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise PublishError(
                f"git {' '.join(args)} 失败（exit {result.returncode}）：\n"
                f"{(result.stderr or result.stdout).strip()[:600]}"
            )
        return result.stdout
    finally:
        askpass.unlink(missing_ok=True)


def ensure_repo(owner: str, repo: str, token: str, *, create: bool) -> None:
    """确认仓库存在；`--create-repo` 时按需创建。"""
    try:
        api_request("GET", f"/repos/{owner}/{repo}", token)
        print(f"  ✓ 仓库已存在：{owner}/{repo}")
        return
    except PublishError as exc:
        if "HTTP 404" not in str(exc):
            raise
    if not create:
        raise PublishError(
            f"仓库 {owner}/{repo} 不存在。到 https://github.com/new 建一个空仓库"
            "（不要勾 README / .gitignore），或加 --create-repo 让本脚本代建。"
        )
    login = api_request("GET", "/user", token).get("login")
    if login and login.lower() != owner.lower():
        print(f"  提示：令牌属于 {login}，而目标 owner 是 {owner}（组织仓库需要相应权限）")
    api_request("POST", "/user/repos", token, payload={"name": repo, "private": False})
    print(f"  ✓ 已创建仓库：{owner}/{repo}")


def push(owner: str, repo: str, token: str) -> None:
    """设远端并推送分支与 tag。地址在本文件里构造（见模块 docstring 第 2 条）。"""
    remote_url = f"https://github.com/{owner}/{repo}.git"
    existing = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if existing.returncode == 0:
        if existing.stdout.strip() != remote_url:
            git("remote", "set-url", "origin", remote_url, token=token)
            print(f"  ✓ 已更正 origin（原为 {existing.stdout.strip()}）")
        else:
            print("  ✓ origin 已指向目标仓库")
    else:
        git("remote", "add", "origin", remote_url, token=token)
        print("  ✓ 已添加 origin")

    tag = f"v{__version__}"
    git("push", "-u", "origin", "HEAD:refs/heads/main", token=token)
    print("  ✓ 已推送分支 main")
    git("push", "origin", f"refs/tags/{tag}", token=token)
    print(f"  ✓ 已推送 tag {tag}")


def ensure_release(owner: str, repo: str, token: str, tag: str, notes: str) -> int:
    """建 Release（已存在则复用），返回 release id。"""
    try:
        release = api_request(
            "POST",
            f"/repos/{owner}/{repo}/releases",
            token,
            payload={"tag_name": tag, "name": tag, "body": notes, "draft": False},
        )
        print(f"  ✓ 已创建 Release {tag}")
        return int(release["id"])
    except PublishError as exc:
        if "HTTP 422" not in str(exc):  # 422 = tag 已存在 Release
            raise
    found = api_request("GET", f"/repos/{owner}/{repo}/releases/tags/{tag}", token)
    print(f"  ✓ Release {tag} 已存在，复用")
    return int(found["id"])


def upload_asset(owner: str, repo: str, token: str, release_id: int, path: Path) -> None:
    """上传（或覆盖）一个附件。同名附件先删，避免 `--clobber` 语义分散在两处。"""
    existing = api_request("GET", f"/repos/{owner}/{repo}/releases/{release_id}/assets", token)
    for asset in existing if isinstance(existing, list) else []:
        if asset.get("name") == path.name:
            api_request("DELETE", f"/repos/{owner}/{repo}/releases/assets/{asset['id']}", token)
            print(f"     （已删除同名旧附件 {path.name}）")
    size_mb = path.stat().st_size / 1024 / 1024
    print(f"  ↑ 上传 {path.name}（{size_mb:.1f}MB）…")
    api_request(
        "POST",
        f"{UPLOADS}/repos/{owner}/{repo}/releases/{release_id}/assets?name={path.name}",
        token,
        raw=path.read_bytes(),
        content_type="application/octet-stream",
    )
    print(f"    ✓ {path.name}")


def regenerate_manifest(owner: str, repo: str, tag: str) -> Path:
    """用真实下载地址重新生成清单（见模块 docstring 第 4 条）。"""
    zip_path = ROOT / "dist" / "hunter1-win32.zip"
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib.util

    spec = importlib.util.spec_from_file_location("mm", ROOT / "scripts" / "make_manifest.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    url_base = f"https://github.com/{owner}/{repo}/releases/download/{tag}"
    manifest = module.build_manifest(
        version=__version__,
        assets=[("win32", zip_path)],
        url_for=lambda _p, name: f"{url_base}/{name}",
        notes=f"hunter1 {tag}",
    )
    # 回读一次：产物必须能被消费端模型接受。
    ReleaseManifest.model_validate(manifest.model_dump(exclude_none=True))
    out = ROOT / "dist" / "manifest.json"
    out.write_text(
        json.dumps(manifest.model_dump(exclude_none=True), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  ✓ 清单已用真实地址重新生成：{manifest.assets[0].url}")
    return out


def main(argv: list[str] | None = None) -> int:
    _enable_utf8_output()
    parser = argparse.ArgumentParser(prog="gh_publish.py", description="推送并建 Release")
    parser.add_argument(
        "owner_repo",
        nargs="?",
        default="",
        help="owner/repo；省略时用「令牌对应账号 + --repo」",
    )
    parser.add_argument("--owner", default="", help="所属账号/组织（默认取令牌账号）")
    parser.add_argument("--repo", default="hunter1", help="仓库名（省略 owner_repo 时用）")
    parser.add_argument("--create-repo", action="store_true", help="仓库不存在时自动创建")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要做什么，不改远端")
    args = parser.parse_args(argv)

    # owner/repo 的确定顺序：位置参数 → --owner/--repo → 令牌账号。
    # 令牌账号放最后，是为了让「双击运行」不必事先知道自己的账号名叫什么 ——
    # 令牌一问，账号自明。
    try:
        owner, repo = resolve_target(args.owner_repo, args.owner, args.repo)
    except PublishError as exc:
        print(exc, file=sys.stderr)
        return 2

    tag = f"v{__version__}"
    print(f"== 发布 {tag} 到 {owner or '<令牌对应账号>'}/{repo} ==")

    zip_path = ROOT / "dist" / "hunter1-win32.zip"
    exe_path = ROOT / "dist" / "hunter1" / "hunter1.exe"
    for f in (zip_path, exe_path):
        if not f.is_file():
            print(f"✗ 缺少产物：{f}（先跑 scripts/build.py --zip）", file=sys.stderr)
            return 1

    # 工作区干净 + tag 指向 HEAD：与 release.sh 同一套前置条件。
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if dirty:
        print(f"✗ 工作区有未提交改动，产物会与 tag 对不上：\n{dirty}", file=sys.stderr)
        return 1
    tag_commit = subprocess.run(
        ["git", "rev-parse", f"{tag}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if tag_commit.returncode != 0:
        print(f"✗ tag {tag} 不存在。先打：git tag -a {tag} -m '…'", file=sys.stderr)
        return 1
    if tag_commit.stdout.strip() != head.stdout.strip():
        # 先取出短 sha 再拼消息：直接内联会让这一行超过 100 列
        # （ruff 的 E501 按东亚宽字符算 2 列，中文串很容易超）。
        tag_sha, head_sha = tag_commit.stdout.strip()[:7], head.stdout.strip()[:7]
        print(f"✗ tag {tag} 未指向 HEAD（{tag_sha} vs {head_sha}）", file=sys.stderr)
        return 1
    print("  ✓ 工作区干净，tag 指向 HEAD")

    if args.dry_run:
        # 省略 owner 时（dry-run 不读令牌）显示占位 —— 否则 "/hunter1" 看起来
        # 像路径写错了，而不是「稍后由令牌补上」。
        target = f"{owner or '「令牌对应账号」'}/{repo}"
        print("\n[dry-run] 将执行：")
        print(f"  1. 确保仓库 {target} 存在" + ("（不存在则创建）" if args.create_repo else ""))
        print(f"  2. git push origin HEAD:refs/heads/main 与 refs/tags/{tag}")
        print(f"  3. 建 Release {tag}")
        print("  4. 重新生成清单（真实地址）并上传 hunter1-win32.zip + manifest.json")
        print("\n[dry-run] 结束 —— 未做任何改动。")
        return 0

    try:
        token = read_token()
        login = api_request("GET", "/user", token).get("login") or ""
        if not owner:
            if not login:
                print("无法确定仓库归属，请显式给出 owner/repo 或 --owner", file=sys.stderr)
                return 2
            owner = login  # 省略 owner 时用令牌账号 —— 双击场景的默认路径
        print(f"  ✓ 令牌有效，账号：{login}")

        ensure_repo(owner, repo, token, create=args.create_repo)
        push(owner, repo, token)
        release_id = ensure_release(
            owner, repo, token, tag, notes=f"hunter1 {tag}\n\n见仓库 README 与 docs/。"
        )
        manifest_path = regenerate_manifest(owner, repo, tag)
        upload_asset(owner, repo, token, release_id, zip_path)
        upload_asset(owner, repo, token, release_id, manifest_path)
    except PublishError as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        return 1

    base = f"https://github.com/{owner}/{repo}/releases/download/{tag}"
    print("\n== 已发布。验收（第一次真跑发布管线）==")
    print(f"  新版本 exe：hunter1 update --source {base}/manifest.json  → 期望「已是最新」")
    print("  旧版本 exe：同上加 --download → 期望「可更新」+ 解压成功")
    print("  改坏清单一个 sha256 字符 → 期望 checksum_mismatch 且不留半包")
    print("\n  稳定入口（写进 HUNTER1_UPDATE_SOURCE，一次设好终身有效）：")
    print(f"    https://github.com/{owner}/{repo}/releases/latest/download/manifest.json")
    print("\n  ⚠ 别忘了去 GitHub 撤销本次用的令牌，并删除本地令牌文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
