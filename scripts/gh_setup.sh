#!/usr/bin/env bash
# 把 GitHub 令牌安全地交给本地发布流程。
#
#   bash scripts/gh_setup.sh
#
# 令牌**只**经过 stdin（`read -s`，不回显、不进 shell 历史、不写进本脚本），
# 最后交给 Git 凭据管理器保存。仓库与用户目录都不会留下明文令牌文件。
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

if [[ -z "$(git config --get-all credential.helper 2>/dev/null)" ]]; then
  echo "✗ 没有配置 Git credential helper，无法安全保存令牌。" >&2
  echo "  Windows 请确认 Git Credential Manager 可用；其他平台请配置 credential.helper。" >&2
  echo "  也可以不使用持久保存，直接设置 GITHUB_TOKEN 环境变量。" >&2
  exit 2
fi

HELPER="$(git config --get-all credential.helper | tail -n 1)"
echo "令牌将保存到 Git 凭据管理器：$HELPER"
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
# `|| true`：EXIT trap 的最后一条命令失败会覆盖脚本退出码 —— 清理失败不该改变结论。
trap 'command rm -rf "$TMP_DIR" 2>/dev/null || true' EXIT
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

if ! printf 'protocol=https\nhost=github.com\nusername=%s\npassword=%s\n\n' \
  "$LOGIN" "$TOKEN" | git credential approve; then
  echo "✗ 写入 Git 凭据管理器失败。" >&2
  exit 1
fi

echo "✓ 令牌有效，账号：$LOGIN"
echo "✓ 已保存到 Git 凭据管理器"
echo ""
echo "标准发布不依赖本机令牌："
echo "  1. 合并版本 PR"
echo "  2. git tag -a vX.Y.Z -m 'hunter1 X.Y.Z'"
echo "  3. git push origin vX.Y.Z"
echo "GitHub Actions 会使用仓库自己的 GITHUB_TOKEN 构建并发布。"
echo ""
echo "令牌留着给后续发版复用；不再发版时先到 GitHub 撤销，再删除凭据："
printf '%s\n' "  printf 'protocol=https\\nhost=github.com\\n\\n' | git credential reject"
