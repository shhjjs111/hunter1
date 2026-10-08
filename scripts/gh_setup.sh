#!/usr/bin/env bash
# 把 GitHub 令牌安全地交给本地发布流程。
#
#   bash scripts/gh_setup.sh
#
# 令牌**只**经过 stdin（`read -s`，不回显、不进 shell 历史、不写进本脚本），
# 存到仓库**外**的用户主目录（默认 `~/.hunter1_gh_token`，权限 600）。
# 本脚本与仓库里任何文件都不含令牌明文。
#
# 为什么不用「把令牌贴进对话」：那是一份会留在聊天记录里的拷贝，而 GitHub 会
# 自动吊销出现在公开内容中的令牌 —— 既泄漏又必然失效。
#
# 校验通过后打印账号名与令牌的作用域，你可以据此确认权限够不够发版（需要
# Contents 读写；若要自动建仓库还需 Administration 读写）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -z "${PY:-}" ]]; then
  if [[ -x "$ROOT/.tools/python/python.exe" ]]; then
    PY="$ROOT/.tools/python/python.exe"
  else
    PY="python"
  fi
fi

TOKEN_FILE="${HUNTER1_GH_TOKEN_FILE:-$HOME/.hunter1_gh_token}"

echo "令牌将存到：$TOKEN_FILE"
echo "（这个位置在仓库之外，不会被 git 提交）"
echo ""
printf '请粘贴 GitHub 令牌后回车（输入不回显）：'
IFS= read -rs TOKEN
echo ""
echo ""

if [[ -z "$TOKEN" ]]; then
  echo "✗ 没有读到内容。" >&2
  exit 2
fi

# 常见误贴：把 "GITHUB_TOKEN=" 前缀一起复制进来。
case "$TOKEN" in
  GITHUB_TOKEN=*|github_token=*)
    TOKEN="${TOKEN#*=}"
    echo "  提示：已去掉开头的 GITHUB_TOKEN= 前缀。"
    ;;
esac

# 令牌里不该有空白；用 read -r 后仍可能带尾随空格（某些终端粘贴行为）。
TOKEN="${TOKEN//[[:space:]]/}"

echo "读到 ${#TOKEN} 个字符，正在向 GitHub 校验…"

# 校验：写临时文件传入，避免令牌出现在进程列表（`curl -H` 会把头写进 argv，
# 同机其它进程可见）。用完立即删除。
# 临时目录 + 宿主路径：给**原生工具**（curl / python）的路径在 MSYS 路径转换
# 关闭时不会被转换（`MSYS_NO_PATHCONV=1`），`/tmp/...` 会被 curl 当成非法路径，
# 以 exit 26 失败 —— 而 `|| echo "000"` 把它变成一个「HTTP 000」，脚本据此报
# 「GitHub 拒绝了该令牌」，**有效令牌永远存不下来**，还引导用户去重建令牌。
# 同因问题见 check.sh:50-58。有 cygpath 才转（Linux/CI 本就是宿主形式）。
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
TMP_AUTH="$TMP_DIR/auth"
TMP_WHO="$TMP_DIR/who.json"

native_path() {
  local path="$1"
  if [[ "$path" == /* ]] && command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$path" 2>/dev/null || printf '%s' "$path"
  else
    printf '%s' "$path"
  fi
}
printf 'Authorization: Bearer %s\n' "$TOKEN" > "$TMP_AUTH"
chmod 600 "$TMP_AUTH"

HTTP="$(
  curl -s -o "$(native_path "$TMP_WHO")" -w '%{http_code}' \
    -H "@$(native_path "$TMP_AUTH")" \
    -H 'Accept: application/vnd.github+json' \
    -H 'X-GitHub-Api-Version: 2022-11-28' \
    https://api.github.com/user || echo "000"
)"

if [[ "$HTTP" != "200" ]]; then
  echo "✗ GitHub 拒绝了该令牌（HTTP $HTTP）。" >&2
  echo "  常见原因：已过期 / 已被撤销 / 复制时漏了字符。" >&2
  echo "  请到 GitHub → Settings → Developer settings → Personal access tokens 重建。" >&2
  exit 1
fi

LOGIN="$("$PY" -c "
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
print(d.get('login') or '(未知)')
" "$(native_path "$TMP_WHO")" 2>/dev/null || echo '(解析失败)')"

# 落盘（600 权限）。先写临时文件再 mv，避免留下半截内容。
umask 077
printf '%s' "$TOKEN" > "$TOKEN_FILE.tmp"
mv "$TOKEN_FILE.tmp" "$TOKEN_FILE"

echo "✓ 令牌有效，账号：$LOGIN"
echo "✓ 已保存到 $TOKEN_FILE（权限 $(stat -c '%a' "$TOKEN_FILE" 2>/dev/null || echo '600')）"
echo ""
echo "接着可以跑："
echo "  bash scripts/release.sh <owner/repo>"
echo ""
echo "令牌留着给后续发版复用；不再发版时再撤销它并删除："
echo "  rm -f \"$TOKEN_FILE\""
