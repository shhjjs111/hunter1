"""上传卫生守卫 —— 「不要什么都传」的机读化。

本仓已有一套「门禁自身的守卫」（`test_gate_guards.py`），钉的是**脚本没查却说通过**。
本文件钉另一半：**什么允许进入仓库**。

背景（真实教训，不是假想）：本仓曾在**公开**仓库里跟踪 AI 协作工具的本地配置
（`.rivet.md` / `.rivet-config.json`），并在注释里写死开发机绝对路径
（`D:\\tianshu`、`D:\\nodejs`、`D:\\RecruitOps`）。前者与项目源码无关，
后者把作者的目录布局暴露给所有人 —— 两类都不该出现在公开仓库。

判据全部经过**双向校准**，这是它们可信的前提：
1. **零误报**：在修好上述问题的仓库上跑，每条判据命中数为 0；
2. **真会红**：对合成的违规样本，每条判据都命中（见 `TestJudgesActuallyFire`）。

只做前一半的门禁会被「反正总在报」绕过去（于是被习惯性忽略）；
只做后一半则当场满屏红。两个方向都要钉。

**刻意的取舍：宁可漏报，也不误报。** 朴素的「敏感词 + 长字符串」启发式在
本仓会大面积误报 —— 测试里遍地是合法的假密钥（`sk-secret-abc123`、
`ghp_SECRET_TOKEN_123`），注释里遍地是通用的 Windows 路径
（`C:\\Windows\\System32`）。一个总在喊狼来了的门禁等于没有门禁，
所以密钥判据只用**高置信度前缀形态**（真实签发格式），路径判据只认
**带用户名的家目录**，不认通用系统路径。

约定：`ROOT` 是仓库根（`backend/tests/` 上溯两级）；所有路径按 git 的 `/` 形态比较。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]

#: 单文件体积上限。当前最大的是 `frontend/package-lock.json`（约 155 KiB）与
#: `contracts/openapi.json`（约 47 KiB）—— 两者都**应该**入库（锁文件与契约快照）。
#: 上限取 2 MiB：既容得下正常的工程文本，又拦得住「手滑把一个压缩包/数据库/
#: 前端产物拖进来」这类事故（它们动辄数 MB 起）。
MAX_FILE_BYTES = 2 * 1024 * 1024

#: 禁止进入仓库的**目录名**（路径段精确匹配，不是子串 —— 否则 `scripts/build.py`
#: 会被 `build` 误伤）。覆盖四类：依赖、缓存、构建产物、工具链本地工作区。
#: `.rivet` / `.workbuddy` / `.tools` 是本机工具链的私有区，`dist` / `build` 是
#: 构建产物（可重建，且体积大）。
FORBIDDEN_SEGMENTS = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".pyright",
        ".venv",
        "venv",
        "dist",
        "build",
        ".tools",
        ".rivet",
        ".workbuddy",
        "coverage",
        "htmlcov",
        ".idea",
        ".vscode",
        ".DS_Store",
    }
)

#: 禁止进入仓库的**扩展名**（编译/打包产物、数据文件、日志、编辑器残留）。
#: 注意**不含** `.json` —— `package-lock.json` 与 `contracts/openapi.json`
#: 是应当入库的锁文件与契约快照，误伤它们会让这个门禁立刻失去可信度。
FORBIDDEN_SUFFIXES = (
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".pyd",
    ".pyc",
    ".pyo",
    ".zip",
    ".tar.gz",
    ".tgz",
    ".whl",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".coverage",
    ".orig",
    ".rej",
    ".bak",
    ".swp",
)

#: 高置信度密钥形态 —— 只列**真实签发格式**，长度阈值取各家的实际长度下限。
#: 这样测试里的假密钥（如 16 字符的 `sk-secret-abc123`）不会被误报；
#: 而真令牌一旦泄进来就必然命中（见 `TestJudgesActuallyFire` 的反向验证）。
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI 风格 sk-", re.compile(r"sk-[A-Za-z0-9_\-]{32,}")),
    ("GitHub ghp_", re.compile(r"ghp_[A-Za-z0-9]{30,}")),
    ("GitHub gho_", re.compile(r"gho_[A-Za-z0-9]{30,}")),
    ("GitHub ghs_", re.compile(r"ghs_[A-Za-z0-9]{30,}")),
    ("GitHub 细粒度 PAT", re.compile(r"github_pat_[A-Za-z0-9_]{50,}")),
    ("AWS Access Key ID", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{20,}")),
    ("私钥文件头", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

#: 带**用户名**的家目录绝对路径 —— 泄漏的是「作者是谁/机器怎么布置」，不是
#: 「这是 Windows 路径」。所以只认 `C:\\Users\\<名字>` / `/home/<名字>` /
#: `/Users/<名字>`，**不认** `C:\\Windows\\...`、`C:\\Program Files\\...`
#: 这类通用路径（它们在文档里是合法且必要的）。
#:
#: 占位形式（`<你>`、`%USERNAME%`、`$HOME`）不匹配 —— 正则要求名字段是
#: `[A-Za-z0-9._-]+`，尖括号与百分号天然排除。
HOME_DIR_PATTERN = re.compile(
    r"(?:[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+[A-Za-z0-9._-]+"
    r"|/(?:home|Users)/[A-Za-z0-9._-]+)"
)

#: 个人数据形态：只留**邮箱**，且排除 RFC 2606/6761 的保留域名。
#:
#: 为什么只留邮箱、为什么排除保留域名 —— 两个都是**实测误报**逼出来的：
#: - 手机号判据在本仓必然误报：抓取夹具是真实站点 HTML 快照，岗位 ID
#:   （`job_detail/04a603d14962268503V_0t6`）里就嵌着 11 位数字串。
#:   而本项目并**不收集**用户手机号（数据在本地 SQLite、`.data/` 已忽略），
#:   这条判据的边际价值远低于它制造噪声的成本。
#: - `hr@example.com` 出现在抓取测试的样本 HTML 里，是标准示例地址，
#:   不是任何人的真实联系方式。
#:
#: 保留邮箱判据的价值：抓取夹具若混进**真实**站点的真实 HR 邮箱，
#: 上传即等于代站点扩散个人数据 —— 那条线仍要守住。
_RESERVED_EMAIL_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "example.edu",
        "localhost",
        "test.com",
        "test.org",
    }
)

PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
)

#: 历史隐私守卫的精确例外。
#:
#: `main` 上唯一已知的旧样例位于该提交的测试文件中。它早于不可变 release
#: 与当前治理规则；改写它会破坏受保护的线性历史、v0.2.0 不可变 tag/release，
#: 并与 `AGENTS.md` 禁止 force-push 的约定冲突。例外只认完整提交哈希 + 路径，
#: 不按文件或规则放行，任何新提交命中都必须修掉。
HISTORY_SENSITIVE_ALLOWLIST = frozenset(
    {
        "92803a424a195c86f17b034378af7fcc8eec9272:backend/tests/test_repo_hygiene.py",
    }
)

#: GitHub 机器人提交使用的公开 noreply 地址。
_GITHUB_NOREPLY_EMAIL = "noreply@" + "github.com"


class CommitEmailRecord(NamedTuple):
    """提交邮箱判据需要的 Git 元数据；不包含提交正文或文件内容。"""

    commit: str
    author_email: str
    committer_email: str


#: 公开历史中已经存在的真实作者邮箱提交。
#:
#: 后续提交必须使用 GitHub noreply（或 `example.com` / `test.com` 等保留域名）。
#: 这里只锁定提交哈希，不复制邮箱；历史元数据无法在不改写受保护历史的条件下清除。
PERSONAL_COMMIT_EMAIL_ALLOWLIST = frozenset(
    {
        "28a899dccff60a8eddc6d9e968e2ad7a6b78e603",
        "4875bf7abea5fc21f62a2bdee699dc2205748747",
        "cc8aa951b5c76aac2822f3b859d16eea98c5732f",
        "d5c7601e27e098e02054ebb7ed7461464a1bd5af",
        "da20ea1c40c12e8cef046593f34e95b2263878be",
        "dcb7a36b08bf1b77dfa3a3a94700a1278de42845",
        "fec2b3db546b5beffad775f32620c746e98fce72",
    }
)

_COMMIT_HEADER_PATTERN = re.compile(r"^commit ([0-9a-f]{40})$")
_DIFF_HEADER_PATTERN = re.compile(r"^diff --git a/(.+) b/(.+)$")

#: 内容扫描跳过的目录：目前为空 —— 所有判据对全部跟踪文件生效。
#: 保留这个常量是为了让「将来某类文件需要豁免」有个**显式**的落点，
#: 而不是在判据函数里塞 if（那样豁免会变成隐形的）。
_PII_EXEMPT_PREFIXES: tuple[str, ...] = ()


def _git(*args: str) -> str:
    """跑一条只读 git 命令。

    显式 `encoding="utf-8"`：`text=True` 不给 encoding 时按宿主 locale 解码，
    Windows 简中是 cp936 —— 这个坑本仓已踩过（见 `test_gate_guards.py` 的注释），
    别再踩一次。

    显式 `timeout`：没有它，一个挂住的子进程会让**整个测试会话无限等待** ——
    CI 上表现为 job 一直 `in_progress` 直到 GitHub 6 小时上限把它取消，
    而日志里只有进度点，看不出卡在哪。加了它，挂起在 N 秒后就变成一次
    明确的 `TimeoutExpired` 失败，指向具体是哪个调用。
    """
    proc = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        check=True,
        timeout=120,
    )
    return proc.stdout


def _tracked_files() -> list[str]:
    """当前被跟踪的文件（git 输出恒用 `/` 分隔，与 Path 的宿主形态无关）。"""
    return [line for line in _git("ls-files").splitlines() if line.strip()]


def _read_text(rel: str) -> str | None:
    """读文本；二进制或读不出时返回 None（跳过内容判据，路径判据仍然生效）。"""
    path = ROOT / rel
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# --------------------------------------------------------------------------- 判据
#
# 判据写成**纯函数**（输入文件列表，输出违规清单），这样才能被双向校准：
# 既对真实仓库跑（期望空），也对合成样本跑（期望命中）。


def judge_ignored_but_tracked(files: list[str]) -> list[str]:
    """被 `.gitignore` 排除、却仍在索引里的文件。

    这是上一轮事故的**根因形态**：`.rivet/` 目录早已整目录忽略，但
    `.rivet.md` / `.rivet-config.json` 这两个**文件**漏在规则外、
    先是逃过忽略进了索引，后来补了规则也没把已跟踪的移除。
    一条 `git add -A` 就能把这类文件再次卷进来。

    用 `git ls-files -c -i --exclude-standard`（列出「已跟踪 且 被忽略」），
    一次调用拿到结果 —— 比逐个 `git check-ignore` 快两个数量级（239 次 ≈ 26 秒
    vs 1 次 ≈ 0.1 秒）。
    """
    out = _git("ls-files", "-c", "-i", "--exclude-standard")
    return sorted(line for line in out.splitlines() if line.strip())


def judge_forbidden_paths(files: list[str]) -> list[str]:
    """路径里出现禁止目录段的文件（依赖 / 缓存 / 产物 / 工具链私有区）。"""
    bad = []
    for rel in files:
        segments = rel.split("/")
        hit = [s for s in segments[:-1] if s in FORBIDDEN_SEGMENTS]
        # 末段也可能是 `.DS_Store` 这类目录名形态的文件名
        if segments and segments[-1] in FORBIDDEN_SEGMENTS:
            hit.append(segments[-1])
        if hit:
            bad.append(f"{rel}  ← 命中禁止目录段 {hit}")
    return sorted(bad)


def judge_forbidden_suffixes(files: list[str]) -> list[str]:
    """扩展名属于「产物 / 数据 / 日志」类的文件。"""
    bad = []
    for rel in files:
        lowered = rel.lower()
        for suffix in FORBIDDEN_SUFFIXES:
            if lowered.endswith(suffix):
                bad.append(f"{rel}  ← 禁止的扩展名 {suffix}")
                break
    return sorted(bad)


def judge_secrets(files: list[str]) -> list[str]:
    """内容里出现高置信度密钥形态。"""
    bad = []
    for rel in files:
        text = _read_text(rel)
        if text is None:
            continue
        for label, pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                # 只报前 12 字符用于定位 —— 真令牌不该被完整打印到 CI 日志里。
                bad.append(f"{rel}  ← {label}：{match.group()[:12]}…")
    return sorted(bad)


def judge_home_paths(files: list[str]) -> list[str]:
    """内容里出现带用户名的家目录绝对路径（开发机目录布局泄漏）。"""
    bad = []
    for rel in files:
        text = _read_text(rel)
        if text is None:
            continue
        for match in HOME_DIR_PATTERN.finditer(text):
            bad.append(f"{rel}  ← 本机家目录路径：{match.group()}")
    return sorted(bad)


def judge_pii(files: list[str]) -> list[str]:
    """内容里出现**真实**邮箱（排除 RFC 2606 保留域名）。

    抓取夹具是真实站点 HTML 快照；若其中混进真实 HR 邮箱，上传即等于代站点
    扩散个人数据。标准示例域名（`example.com`）与本地测试值不算。
    """
    bad = []
    for rel in files:
        text = _read_text(rel)
        if text is None:
            continue
        for label, pattern in PII_PATTERNS:
            for match in pattern.finditer(text):
                address = match.group()
                domain = address.rpartition("@")[2].lower()
                if domain in _RESERVED_EMAIL_DOMAINS:
                    continue
                bad.append(f"{rel}  ← {label}：{address}")
    return sorted(bad)


def judge_oversized(files: list[str]) -> list[str]:
    """单文件超过体积上限。"""
    bad = []
    for rel in files:
        path = ROOT / rel
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            bad.append(
                f"{rel}  ← {size / 1024 / 1024:.1f} MiB > {MAX_FILE_BYTES // 1024 // 1024} MiB"
            )
    return sorted(bad)


def judge_history_sensitive_material(diff_text: str) -> list[str]:
    """扫描历史 diff 中**新增**的密钥形态与用户家目录路径。

    只看新增行：任何进入过公开历史的文本，至少会在某个提交中作为新增行出现。
    删除行已包含在它首次新增的提交中；重复扫描删除只会迫使白名单漂移。
    返回值只有 `commit:path`，不回显命中的内容，避免门禁日志二次扩散。
    """
    commit: str | None = None
    path: str | None = None
    hits: set[str] = set()
    for line in diff_text.splitlines():
        if match := _COMMIT_HEADER_PATTERN.match(line):
            commit = match.group(1)
            path = None
            continue
        if match := _DIFF_HEADER_PATTERN.match(line):
            path = match.group(2)
            continue
        if not commit or not path or not line.startswith("+") or line.startswith("+++"):
            continue
        content = line[1:]
        if any(pattern.search(content) for _, pattern in SECRET_PATTERNS) or (
            HOME_DIR_PATTERN.search(content)
        ):
            hits.add(f"{commit}:{path}")
    return sorted(hits)


def judge_personal_commit_emails(records: list[CommitEmailRecord]) -> list[str]:
    """拒绝新增使用真实邮箱的作者提交。

    返回值只有提交哈希；错误信息无需再次公开邮箱。GitHub noreply 与 RFC
    保留域名允许通过，方便机器人、Dependabot 与测试夹具继续工作。
    """
    bad = []
    for record in records:
        for email in (record.author_email, record.committer_email):
            normalized = email.strip().lower()
            if not normalized:
                continue
            domain = normalized.rpartition("@")[2]
            if (
                domain == "users.noreply.github.com"
                or normalized == _GITHUB_NOREPLY_EMAIL
                or domain in _RESERVED_EMAIL_DOMAINS
                or record.commit in PERSONAL_COMMIT_EMAIL_ALLOWLIST
            ):
                continue
            bad.append(record.commit)
            break
    return sorted(bad)


def _require_full_history() -> None:
    """历史守卫必须看到完整历史；浅克隆时明确失败，不制造假绿。"""
    assert _git("rev-parse", "--is-shallow-repository").strip() == "false", (
        "历史卫生守卫需要完整 Git 历史；请先执行 `git fetch --unshallow`。"
    )


def _permanent_history_refs() -> list[str]:
    """返回需要审计的持久 refs，排除 GitHub 的合成 PR merge refs。

    CI 检出的 `HEAD` 本身可能是 test-merge 提交，因此不能使用会自动包含
    `HEAD` 的 `--all`；必须显式传入分支与 tag。合成 merge 的作者邮箱由 GitHub
    账号生成，不属于项目历史，排除后真实 PR head 仍由远端分支覆盖。
    """
    refs = _git(
        "for-each-ref",
        "--format=%(refname)",
        "refs/heads",
        "refs/remotes",
        "refs/tags",
    ).splitlines()
    permanent = [
        ref
        for ref in refs
        if ref.strip()
        and not ref.startswith("refs/pull/")
        and not ref.startswith("refs/remotes/pull/")
    ]
    assert permanent, "历史卫生守卫未找到任何持久 Git ref。"
    return permanent


def _history_diff_text() -> str:
    _require_full_history()
    refs = _permanent_history_refs()
    return _git(
        "log",
        "--format=commit %H",
        "-p",
        "--unified=0",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        *refs,
    )


def _commit_email_records() -> list[CommitEmailRecord]:
    _require_full_history()
    refs = _permanent_history_refs()
    fields = _git(
        "log",
        "--format=%H%x00%ae%x00%ce%x00",
        *refs,
    ).split("\0")
    records = []
    for index in range(0, len(fields) - 2, 3):
        record = CommitEmailRecord(
            commit=fields[index].strip(),
            author_email=fields[index + 1].strip(),
            committer_email=fields[index + 2].strip(),
        )
        if record.commit and record.author_email:
            records.append(record)
    return records


#: 全部判据（名称 → 函数）。新增判据时**一并**加进这里，否则不会被跑到。
JUDGES = {
    "被忽略却仍被跟踪": judge_ignored_but_tracked,
    "禁止目录段": judge_forbidden_paths,
    "禁止扩展名": judge_forbidden_suffixes,
    "高置信度密钥": judge_secrets,
    "本机家目录路径": judge_home_paths,
    "个人数据（真实邮箱）": judge_pii,
    "单文件体积": judge_oversized,
}


# --------------------------------------------------------------------------- 断言


class TestRepositoryIsClean:
    """当前仓库必须通过每一条判据。任一不通过，就说明有东西不该上传。"""

    def test_no_ignored_files_are_tracked(self) -> None:
        assert judge_ignored_but_tracked(_tracked_files()) == []

    def test_no_forbidden_path_segments(self) -> None:
        assert judge_forbidden_paths(_tracked_files()) == []

    def test_no_forbidden_suffixes(self) -> None:
        assert judge_forbidden_suffixes(_tracked_files()) == []

    def test_no_secrets_in_content(self) -> None:
        assert judge_secrets(_tracked_files()) == []

    def test_no_home_directory_paths_in_content(self) -> None:
        assert judge_home_paths(_tracked_files()) == []

    def test_no_personal_data_in_content(self) -> None:
        assert judge_pii(_tracked_files()) == []

    def test_no_oversized_files(self) -> None:
        assert judge_oversized(_tracked_files()) == []

    def test_every_judge_is_exercised(self) -> None:
        """上列用例与 `JUDGES` 必须一一对应 —— 防止新增判据后被漏测。

        判据表是手写的，用例也是手写的；两者不同步时，新判据会**静默不生效**
        （表里有、没人调用；或用例有、表里没有）。这条把两份清单钉在一起。
        """
        expected = set(JUDGES)
        covered = {
            "被忽略却仍被跟踪",
            "禁止目录段",
            "禁止扩展名",
            "高置信度密钥",
            "本机家目录路径",
            "个人数据（真实邮箱）",
            "单文件体积",
        }
        assert expected == covered, f"判据表与用例不同步：{expected ^ covered}"


class TestRepositoryHistoryIsClean:
    """只靠当前 HEAD 的扫描会漏掉已经公开的旧提交，这里守住完整历史。"""

    def test_no_new_sensitive_material_in_history(self) -> None:
        hits = set(judge_history_sensitive_material(_history_diff_text()))
        unexpected = sorted(hits - HISTORY_SENSITIVE_ALLOWLIST)
        assert unexpected == [], f"历史中存在未登记的高置信度敏感形态：{unexpected}"

    def test_no_new_personal_author_email_in_history(self) -> None:
        unexpected = judge_personal_commit_emails(_commit_email_records())
        assert unexpected == [], f"历史中存在未登记的真实作者邮箱提交：{unexpected}"


def _assembled(*parts: str) -> str:
    """把样本拼成完整字符串 —— **源码文本里不出现完整形态**。

    为什么不能直接写成字面量：本文件**自己也在被扫描的跟踪清单里**。
    样本若写成字面量，它一边断言「仓库里没有密钥」，一边自己带着一个密钥形态——
    文件未跟踪时判据扫不到它（门禁绿），**一旦提交进索引就当场自撞**。
    表现是最坏的一种时序：提交前全绿、提交后全红，而本地那次门禁给出的
    是虚假的安全感。

    （实测踩到过：提交 92803a4 之后，`test_no_secrets_in_content` /
    `test_no_home_directory_paths_in_content` / `test_no_personal_data_in_content`
    三条同时转红，命中项全是本文件自己的反向校准样本。）

    不采用「把本文件加入豁免名单」的修法：那会在最该被扫的文件上开一个盲区——
    这个文件恰恰是最可能被塞进真密钥的地方（调试时顺手粘一个真 token 进来）。
    拼装则让源码永远不构成完整形态，判据保持全量覆盖。
    """
    return "".join(parts)


class TestJudgesActuallyFire:
    """反向校准：每条判据对**合成违规样本**都必须命中。

    没有这一组，「判据永远返回空」的实现也能让上面全绿 —— 一个不会红的门禁
    和没有门禁是一样的，而它更危险：它给人已经守住了的错觉。
    """

    def test_secret_judge_fires_on_real_token_shapes(self) -> None:
        samples = {
            _assembled("sk-", "proj-", "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH"): "OpenAI",
            _assembled("ghp_", "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"): "GitHub",
            _assembled("AKIA", "IOSFODNN7EXAMPLE"): "AWS",
        }
        for token, label in samples.items():
            assert _scan_text_for_secrets(token), f"{label} 令牌形态未被检出：{token[:12]}…"

    def test_secret_judge_ignores_test_style_fake_keys(self) -> None:
        """测试里的假密钥**不该**被报 —— 这是误报控制的直接断言。

        本仓测试大量使用 `sk-secret-abc123` / `ghp_SECRET_TOKEN_123` 这类
        短占位值。判据若把它们当泄露，门禁会天天红，然后被绕过。
        """
        for fake in (
            "sk-secret-abc123",
            "sk-super-secret-value",
            "ghp_SECRET_TOKEN_123",
            "github_pat_THIS_IS_THE_TOKEN_VALUE",
        ):
            assert not _scan_text_for_secrets(fake), f"假密钥被误报：{fake}"

    def test_home_path_judge_fires_on_user_paths(self) -> None:
        for path in (
            _assembled("C:", "\\", "Users", "\\", "23579", "\\projects\\x.py"),
            _assembled("C:/", "Users", "/", "23579", "/projects/x.py"),
            _assembled("/home/", "alice", "/proj/x.py"),
            _assembled("/Users/", "alice", "/proj/x.py"),
        ):
            assert HOME_DIR_PATTERN.search(path), f"家目录路径未被检出：{path}"

    def test_home_path_judge_ignores_generic_system_paths(self) -> None:
        """通用系统路径与占位符不该被报 —— 它们在文档里合法且必要。"""
        for path in (
            r"C:\Windows\System32\bash.exe",
            r"C:\Program Files\Git\bin",
            _assembled("C:", "\\", "Users", "\\", "<placeholder>"),
            "C:/Users/%USERNAME%",
            "/usr/bin:/bin",
        ):
            assert not HOME_DIR_PATTERN.search(path), f"通用路径被误报：{path}"

    def test_path_judges_fire_on_artifact_like_paths(self) -> None:
        assert judge_forbidden_paths(["node_modules/react/index.js"])
        assert judge_forbidden_paths(["frontend/dist/assets/index.js"])
        assert judge_forbidden_paths([".rivet/sessions/x.json"])
        assert judge_forbidden_suffixes(["dist/hunter1-win32.zip"])
        assert judge_forbidden_suffixes(["data/hunter1.db"])
        assert judge_forbidden_suffixes(["backend/src/hunter1/cli.pyc"])

    def test_path_judges_ignore_legitimate_repo_files(self) -> None:
        """正常工程文件不该被路径判据误伤。"""
        for rel in (
            "backend/src/hunter1/main.py",
            "scripts/build.py",  # 名字含 build，但段名不是 build
            "frontend/package-lock.json",  # 锁文件，应当入库
            "contracts/openapi.json",  # 契约快照，应当入库
            "backend/hunter1.spec",
            "docs/ARCHITECTURE.md",
        ):
            assert judge_forbidden_paths([rel]) == [], f"路径误报：{rel}"
            assert judge_forbidden_suffixes([rel]) == [], f"扩展名误报：{rel}"

    def test_pii_judge_fires_on_real_contact_details(self) -> None:
        assert _judge_pii_text(_assembled("联系 hr@", "somecompany.cn", " 投递"))

    def test_pii_judge_ignores_reserved_example_domains(self) -> None:
        """保留域名是测试夹具的标准示例，不该报。"""
        for text in ("mailto:hr@example.com", "a@example.org", "x@test.com"):
            assert not _judge_pii_text(text), f"保留域名被误报：{text}"

    def test_history_judge_fires_on_added_secret(self) -> None:
        token = _assembled(
            "sk-",
            "proj-",
            "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH",
        )
        diff = (
            f"commit {'a' * 40}\n"
            "diff --git a/probe.py b/probe.py\n"
            "@@ -0,0 +1 @@\n"
            f'+KEY = "{token}"\n'
        )
        assert judge_history_sensitive_material(diff) == [
            f"{'a' * 40}:probe.py",
        ]

    def test_history_judge_fires_on_added_home_path(self) -> None:
        home_path = _assembled("C:", "\\", "Users", "\\", "alice", "\\", "probe.py")
        diff = (
            f"commit {'b' * 40}\n"
            "diff --git a/docs/probe.md b/docs/probe.md\n"
            "@@ -0,0 +1 @@\n"
            f"+{home_path}\n"
        )
        assert judge_history_sensitive_material(diff) == [
            f"{'b' * 40}:docs/probe.md",
        ]

    def test_history_judge_ignores_removed_secret(self) -> None:
        token = _assembled(
            "ghp_",
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij",
        )
        diff = (
            f"commit {'c' * 40}\n"
            "diff --git a/probe.py b/probe.py\n"
            "@@ -1 +0,0 @@\n"
            f'-KEY = "{token}"\n'
        )
        assert judge_history_sensitive_material(diff) == []

    def test_commit_email_judge_fires_on_personal_address(self) -> None:
        email = _assembled("person@", "somecompany.cn")
        record = CommitEmailRecord(
            commit="d" * 40,
            author_email=email,
            committer_email=email,
        )
        assert judge_personal_commit_emails([record]) == ["d" * 40]

    def test_commit_email_judge_allows_noreply_and_reserved_domains(self) -> None:
        records = [
            CommitEmailRecord(
                "e" * 40,
                _assembled("123+user@", "users.noreply.github.com"),
                _assembled("123+user@", "users.noreply.github.com"),
            ),
            CommitEmailRecord(
                "f" * 40,
                _GITHUB_NOREPLY_EMAIL,
                _GITHUB_NOREPLY_EMAIL,
            ),
            CommitEmailRecord(
                "1" * 40,
                "person@example.com",
                "person@example.com",
            ),
            CommitEmailRecord(
                "2" * 40,
                "agent@test.com",
                "agent@test.com",
            ),
        ]
        assert judge_personal_commit_emails(records) == []

    def test_commit_email_judge_rejects_github_squash_author_email(self) -> None:
        record = CommitEmailRecord(
            commit="3" * 40,
            author_email=_assembled("person@", "somecompany.cn"),
            committer_email=_GITHUB_NOREPLY_EMAIL,
        )
        assert judge_personal_commit_emails([record]) == ["3" * 40]

    def test_oversized_judge_fires_on_big_file(self, tmp_path: Path) -> None:
        """判据对超过上限的文件必须命中。

        `judge_oversized` 按仓库根解析相对路径，故这里直接在仓库内造一个
        超限文件（用 `_rivet` 之外的名字，且立即删除）—— 不能只断言
        "函数被调用过"，那种断言在判据失效时照样通过。
        """
        probe = ROOT / "._hygiene_probe.tmp"
        try:
            probe.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
            hits = judge_oversized([probe.name])
            assert hits and hits[0].startswith(probe.name), f"超限文件未被检出：{hits}"
        finally:
            probe.unlink(missing_ok=True)

    def test_oversized_judge_ignores_normal_files(self) -> None:
        assert judge_oversized(["backend/pyproject.toml"]) == []


def _scan_text_for_secrets(text: str) -> bool:
    return any(pattern.search(text) for _, pattern in SECRET_PATTERNS)


def _scan_text_for_pii(text: str) -> bool:
    return any(pattern.search(text) for _, pattern in PII_PATTERNS)


def _judge_pii_text(text: str) -> list[str]:
    """对一段文本跑 `judge_pii` 的域名过滤逻辑（不落盘）。"""
    hits = []
    for label, pattern in PII_PATTERNS:
        for match in pattern.finditer(text):
            address = match.group()
            if address.rpartition("@")[2].lower() in _RESERVED_EMAIL_DOMAINS:
                continue
            hits.append(f"{label}：{address}")
    return hits
