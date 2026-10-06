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
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

from hunter1 import __version__  # noqa: E402
from hunter1.platform.update.rules import ReleaseManifest  # noqa: E402

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
DEFAULT_TOKEN_FILE = Path.home() / ".hunter1_gh_token"


class PublishError(RuntimeError):
    pass


def read_token() -> str:
    """从环境变量或令牌文件取令牌；两者都没有则报错并给出设置方法。"""
    env = os.environ.get("GITHUB_TOKEN", "").strip()
    if env:
        return env
    if DEFAULT_TOKEN_FILE.is_file():
        token = DEFAULT_TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    raise PublishError(
        "没有找到令牌。先跑一次 `bash scripts/gh_setup.sh`（安全地把令牌存到仓库外），"
        "或设 GITHUB_TOKEN 环境变量。"
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
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
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
            ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=False
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
        ["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True, check=False
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
    parser = argparse.ArgumentParser(prog="gh_publish.py", description="推送并建 Release")
    parser.add_argument("owner_repo", help="owner/repo")
    parser.add_argument("--create-repo", action="store_true", help="仓库不存在时自动创建")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要做什么，不改远端")
    args = parser.parse_args(argv)

    if "/" not in args.owner_repo:
        print("owner/repo 格式不对（应形如 acme/hunter1）", file=sys.stderr)
        return 2
    owner, repo = args.owner_repo.split("/", 1)
    tag = f"v{__version__}"

    print(f"== 发布 {tag} 到 {owner}/{repo} ==")

    zip_path = ROOT / "dist" / "hunter1-win32.zip"
    exe_path = ROOT / "dist" / "hunter1" / "hunter1.exe"
    for f in (zip_path, exe_path):
        if not f.is_file():
            print(f"✗ 缺少产物：{f}（先跑 scripts/build.py --zip）", file=sys.stderr)
            return 1

    # 工作区干净 + tag 指向 HEAD：与 release.sh 同一套前置条件。
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()
    if dirty:
        print(f"✗ 工作区有未提交改动，产物会与 tag 对不上：\n{dirty}", file=sys.stderr)
        return 1
    tag_commit = subprocess.run(
        ["git", "rev-parse", f"{tag}^{{commit}}"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if tag_commit.returncode != 0:
        print(f"✗ tag {tag} 不存在。先打：git tag -a {tag} -m '…'", file=sys.stderr)
        return 1
    if tag_commit.stdout.strip() != head.stdout.strip():
        print(f"✗ tag {tag} 未指向 HEAD（{tag_commit.stdout.strip()[:7]} vs {head.stdout.strip()[:7]}）", file=sys.stderr)
        return 1
    print("  ✓ 工作区干净，tag 指向 HEAD")

    if args.dry_run:
        print("\n[dry-run] 将执行：")
        print(f"  1. 确保仓库 {owner}/{repo} 存在" + ("（不存在则创建）" if args.create_repo else ""))
        print(f"  2. git push origin HEAD:refs/heads/main 与 refs/tags/{tag}")
        print(f"  3. 建 Release {tag}")
        print("  4. 重新生成清单（真实地址）并上传 hunter1-win32.zip + manifest.json")
        return 0

    try:
        token = read_token()
        who = api_request("GET", "/user", token)
        print(f"  ✓ 令牌有效，账号：{who.get('login')}")

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
    print(f"\n  稳定入口（写进 HUNTER1_UPDATE_SOURCE，一次设好终身有效）：")
    print(f"    https://github.com/{owner}/{repo}/releases/latest/download/manifest.json")
    print("\n  ⚠ 别忘了去 GitHub 撤销本次用的令牌，并删除本地令牌文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
