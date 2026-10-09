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

# tag 必须与包版本一致。
#
# 清单里的 `version` 取自**源码**（`hunter1.__version__`），而下载地址是
# `.../releases/download/<tag>/<zip>` —— 两个字段由**不同来源**决定。给一个自定义 tag
# 就能产出「版本号 ≠ 下载 URL」的清单，而脚本一路全绿：更新链去另一个 tag 下取产物
# （不存在，或还停在上一次构建），失败发生在**用户端**的自动更新里，本地毫无征兆。
# 这与上面第 1) 步「工作区必须干净」同源：产物与 tag 必须对得上。要换 tag，先改
# `hunter1.__version__`，再走同一套流程。
if [[ "$TAG" != "v$VERSION" ]]; then
  echo "tag 与包版本不一致：tag=$TAG，而 hunter1.__version__=$VERSION（应为 v$VERSION）。" >&2
  echo "  清单的 version 来自源码、下载地址来自 tag —— 不一致会产出一份「版本号 ≠ 下载" >&2
  echo "  URL」的清单，自动更新会静默取到错误的产物（本地全绿，故障在用户端）。" >&2
  exit 2
fi

echo "== 版本：$VERSION（tag $TAG）=="

# 1) 工作区必须干净：带着未提交改动发版，产物就与 tag 对不上。
if [[ -n "$(git status --porcelain)" ]]; then
  echo "✗ 工作区有未提交改动。先提交再发版 —— 否则产物与 tag 指向的代码不一致。" >&2
  git status --short >&2
  exit 1
fi
echo "  ✓ 工作区干净"

# 2) 产物必须存在。
#
# 名字从 `scripts/artifact.py` 取，**不写字面量**：产物按 `sys.platform` 命名
# （`hunter1-win32.zip` / `hunter1-linux.zip`），而 CI 跑在 ubuntu-latest ——
# 写死 win32 会让整条发布链在非 Windows 上必然失败（`pyproject.toml` 却自称
# 跨平台），且这个缺口永远不会被本机门禁发现。
ARTIFACT_NAME="$("$PY" -c "import sys; sys.path.insert(0, 'scripts'); from artifact import artifact_name; print(artifact_name())")"
EXE_REL="$("$PY" -c "import sys; sys.path.insert(0, 'scripts'); from artifact import exe_relative_path; print(exe_relative_path())")"
# 清单里的 `platform` 字段必须与产物名用**同一个**词汇（两者都取自 artifact.py）：
# 写死 `win32=` 时，Linux 上打出的包会被声明成 win32 平台 —— 不报错、不告警，
# 而 Linux 用户的更新端按 `sys.platform`（`linux`）查清单，永远查到「没有本平台
# 产物，跳过」：**所有非 Windows 用户的更新链静默断掉**。这与产物名写死是同一类
# 缺口，只是躺在清单字段上。
PLATFORM_KEY="$("$PY" -c "import sys; sys.path.insert(0, 'scripts'); from artifact import platform_key; print(platform_key())")"
ZIP="dist/$ARTIFACT_NAME"
EXE="dist/$EXE_REL"
for f in "$ZIP" "$EXE"; do
  [[ -f "$f" ]] || { echo "✗ 缺少产物：$f（先跑 scripts/build.py --zip）" >&2; exit 1; }
done
echo "  ✓ 产物存在（$ARTIFACT_NAME）"

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
# 先写**临时文件**，检查通过才落到 dist/manifest.json：污染检查只能基于**生成
# 结果**（URL 长什么样，要看写出来的文件），而生成后直接落盘会让可疑内容留在
# 正式路径上 —— 用户若没细看退出码，就会把那份清单传上去。所以先落到临时位置，
# 检查通过再 mv；异常路径由 trap 清理。
URL_BASE="https://github.com/$OWNER_REPO/releases/download/$TAG"
TMP_MANIFEST="dist/.manifest.tmp.json"
# `|| true`：EXIT trap 的最后一条命令失败会覆盖脚本退出码 —— 清理失败不该改变结论。
trap 'rm -f "$TMP_MANIFEST" 2>/dev/null || true' EXIT

"$PY" scripts/make_manifest.py \
  --asset "$PLATFORM_KEY=$ZIP" \
  --url-base "$URL_BASE" \
  --out "$TMP_MANIFEST"

# 5) 地址污染检查（双保险）。
#
# 事实边界（都实测过，别推错）：
#   - `/mirror china` 的改写发生在「**命令文本**」这一层：命令行里写死的
#     github.com 会被换成 gitcode.com/gh_mirror，`python -c` 里的字符串字面量
#     同样会（`argv` 收到的是改写后的值）。
#   - 但**脚本文件内部**的字符串**不受影响** —— 本脚本上面构造的 URL_BASE 是
#     真的 github.com，实测生成的清单地址正确、无需关镜像。
# 所以这道检查平时不会触发；它的价值在于兜住「将来 URL 改成从参数传入」或
# 「改写机制扩展到文件内容」这两种变化 —— 那时会静默发出一份指向镜像的清单。
if grep -q "gitcode.com\|gh_mirror" "$TMP_MANIFEST"; then
  echo "" >&2
  echo "⚠ 清单里的地址被改写了（含 gitcode.com/gh_mirror）。" >&2
  echo "  原因通常是 URL 经由**命令行参数**传入：/mirror china 会改写命令文本里的" >&2
  echo "  github.com（实测，连 python 源码里的字面量也改）。" >&2
  echo "  处理：先 /mirror default 再重跑；或确认真实下载地址本就走镜像。" >&2
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
echo "     $ZIP"
echo "     dist/manifest.json"
echo "   （装了 gh 的话：gh release create $TAG $ZIP dist/manifest.json \\"
echo "       --title \"hunter1 $TAG\" --notes-file <说明>）"
echo ""
echo "3) 开 tag 保护（Settings → Tags → protect v*）—— 这是更新链信任模型的前提。"
echo ""
echo "4) 发布后验收（第一次真跑发布管线）："
echo "     新版本 exe：hunter1 update --source $URL_BASE/manifest.json  → 期望「已是最新」"
echo "     旧版本 exe：同上加 --download → 期望「可更新」+ 解压成功"
echo "     改坏清单一个 sha256 字符 → 期望 checksum_mismatch 且不留半包"
