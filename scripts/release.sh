#!/usr/bin/env bash
# 发布收尾 —— 把产物 + 清单准备到「可以直接上传」的状态。
#
#   bash scripts/release.sh <owner/repo> [tag]
#
# 为什么需要这个脚本：`manifest.json` 里的 `url` 是**绝对地址**，上传前无从得知，
# 所以必须以真实的 owner/repo 重新生成一次。而这一步最容易忘 —— 忘了的后果是
# 一份指向 `example.invalid` 的清单永久挂在 Release 上（生成器虽会告警，但文件
# 照样产出）。把它固化成一条命令，就不必靠记性。
#
# 脚本只做**本地**的事（校验 + 生成清单 + 打印后续命令），不碰网络、不碰远端：
# push 与上传哪一步都可能需要你的凭据，交回给你执行更稳妥。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# cd 到仓库根后一律用相对路径：本机解释器是 Windows 版 Python，而 Git Bash 的
# `/d/...` 路径它不认（实测 `ModuleNotFoundError`）。相对路径两边都认，最省事。
cd "$ROOT"

if [[ -z "${PY:-}" ]]; then
  if [[ -x "$ROOT/.tools/python/python.exe" ]]; then
    PY="$ROOT/.tools/python/python.exe"
  elif [[ -x "$ROOT/.tools/python/bin/python3" ]]; then
    PY="$ROOT/.tools/python/bin/python3"
  else
    PY="python"
  fi
fi

OWNER_REPO="${1:-}"
TAG="${2:-}"

if [[ -z "$OWNER_REPO" ]]; then
  echo "用法：bash scripts/release.sh <owner/repo> [tag]" >&2
  echo "例：  bash scripts/release.sh acme/hunter1" >&2
  exit 2
fi

if [[ ! "$OWNER_REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
  echo "owner/repo 格式不对：$OWNER_REPO（应为 owner/repo，用正斜杠）" >&2
  exit 2
fi

VERSION="$("$PY" -c "import sys; sys.path.insert(0, 'backend/src'); from hunter1 import __version__; print(__version__)")"
TAG="${TAG:-v$VERSION}"

echo "== 版本：$VERSION（tag $TAG）=="

# 1) 工作区必须干净：带着未提交改动发版，产物就与 tag 对不上。
if [[ -n "$(git status --porcelain)" ]]; then
  echo "✗ 工作区有未提交改动。先提交再发版 —— 否则产物与 tag 指向的代码不一致。" >&2
  git status --short >&2
  exit 1
fi
echo "  ✓ 工作区干净"

# 2) 产物必须存在。
ZIP="dist/hunter1-win32.zip"
EXE="dist/hunter1/hunter1.exe"
for f in "$ZIP" "$EXE"; do
  [[ -f "$f" ]] || { echo "✗ 缺少产物：$f（先跑 scripts/build.py --zip）" >&2; exit 1; }
done
echo "  ✓ 产物存在"

# 3) tag 必须存在且指向 HEAD。
if ! git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "✗ tag $TAG 不存在。先打：git tag -a $TAG -m '…'" >&2
  exit 1
fi
TAG_COMMIT="$(git rev-parse "$TAG^{commit}")"
HEAD_COMMIT="$(git rev-parse HEAD)"
if [[ "$TAG_COMMIT" != "$HEAD_COMMIT" ]]; then
  echo "✗ tag $TAG 指向 ${TAG_COMMIT:0:7}，HEAD 是 ${HEAD_COMMIT:0:7} —— 两者必须一致。" >&2
  echo "  改 tag 指向：git tag -f $TAG" >&2
  exit 1
fi
echo "  ✓ tag 指向 HEAD（${HEAD_COMMIT:0:7}）"

# 4) 生成清单（真实地址）。
#
# 先写**临时文件**，检查通过才落到 dist/manifest.json —— 因为镜像改写发生在
# **参数传递层**：bash 里 URL_BASE 还是 github.com，但 python 收到的 argv 已被
# /mirror china 改成 gitcode.com/gh_mirror（实测）。所以没法在 bash 侧提前查，
# 只能生成后查文件；但查之前不应该让可疑内容落到正式路径上。
URL_BASE="https://github.com/$OWNER_REPO/releases/download/$TAG"
TMP_MANIFEST="dist/.manifest.tmp.json"
trap 'rm -f "$TMP_MANIFEST"' EXIT

"$PY" scripts/make_manifest.py \
  --asset "win32=$ZIP" \
  --url-base "$URL_BASE" \
  --out "$TMP_MANIFEST"

# 5) 地址污染检查。
if grep -q "gitcode.com\|gh_mirror" "$TMP_MANIFEST"; then
  echo "" >&2
  echo "⚠ 清单里的地址被镜像改写了（含 gitcode.com/gh_mirror）。" >&2
  echo "  你现在开着 /mirror china —— 它会改写命令行里的 github.com。" >&2
  echo "  要发到 GitHub 官方地址：先 /mirror default，再重跑本脚本。" >&2
  echo "  （dist/manifest.json 未被改动，仍是上一次的内容。）" >&2
  exit 1
fi

mv "$TMP_MANIFEST" dist/manifest.json
trap - EXIT

echo ""
echo "== 本地已就绪。以下步骤需要你的凭据，请手动执行 =="
echo ""
echo "1) 关联远端并推送（含 tag）："
echo "     git remote add origin https://github.com/$OWNER_REPO.git   # 若已关联则跳过"
echo "     git push -u origin main --tags"
echo ""
echo "2) 建 Release 并上传产物（网页：https://github.com/$OWNER_REPO/releases/new，"
echo "   tag 选 $TAG，把下面两个文件拖进去）："
echo "     dist/hunter1-win32.zip"
echo "     dist/manifest.json"
echo "   （装了 gh 的话：gh release create $TAG dist/hunter1-win32.zip dist/manifest.json \\"
echo "       --title \"hunter1 $TAG\" --notes-file <说明>）"
echo ""
echo "3) 开 tag 保护（Settings → Tags → protect v*）—— 这是更新链信任模型的前提。"
echo ""
echo "4) 发布后验收（第一次真跑发布管线）："
echo "     新版本 exe：hunter1 update --source $URL_BASE/manifest.json  → 期望「已是最新」"
echo "     旧版本 exe：同上加 --download → 期望「可更新」+ 解压成功"
echo "     改坏清单一个 sha256 字符 → 期望 checksum_mismatch 且不留半包"
