"""架构约束的自动化守卫。

把「分层」从口头约定变成**可执行断言** —— 否则它会在某次赶工里悄悄破掉。

规则（迁移期版本；终态随各波次收紧，见 AGENTS.md）：

- `platform`（机制内核）不得 import `hunter1.{slices, application}`。
  过渡期豁免：允许依赖 `hunter1.domain.*`（共享模型 —— Wave 4 后随各切片归位，
  届时本豁免删除）。
- `domain`（纯模型与规则）不得 import `hunter1.{application, slices}`；
  对 platform 只允许 `platform.text`（纯函数）。
- `application`（用例）不得 import `hunter1.{platform, slices}` ——
  只依赖 domain 与端口（Protocol）。
- `slices`（业务切片）不得 import `hunter1.main`；对旧层
  （`hunter1.application`）的依赖必须登记在 `SLICE_LEGACY_ALLOW`（Wave 4 清空）；
  切片之间只经公开面（`__init__`），不得深链其他切片的内部模块；
  **切片间的依赖方向必须落在 `SLICE_DEPENDENCY_ALLOW` 白名单里**
  （AGENTS.md：`crawl/scoring/applications/assistant → jobs`，别的方向都不许）。

**已删除的层不在上表里**：`web/`（Wave 6）与 `crawlers/`（收尾迁移）都已从
代码中移除，由**存在性断言**钉死（`test_legacy_ssr_layer_is_gone` /
`test_legacy_crawlers_layer_is_gone`）。import 层面无需再禁 —— 包不存在时
import 本就会失败；把它们留在禁用表里会读起来像"它们还活着"。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "hunter1"

# 每层禁止的顶层包前缀（迁移期规则；见模块 docstring）
FORBIDDEN_IMPORTS: dict[str, tuple[str, ...]] = {
    "platform": ("hunter1.slices", "hunter1.application", "hunter1.main"),
    "domain": ("hunter1.application", "hunter1.slices", "hunter1.main"),
    "application": (
        "hunter1.platform",
        "hunter1.slices",
        "hunter1.main",
    ),
}

# 切片 → 过渡期登记的旧层依赖（Wave 4 完成后必须清空；新增条目需评审）
SLICE_LEGACY_ALLOW: dict[str, tuple[str, ...]] = {}

# 协议层的共享豁免：`application/ports.py` 里是**进程边界**的抽象
# （Crawler / JobRepository / TextFetcher / LLMProvider），它们不是「某个切片的实现」，
# 而是切片与外部世界之间的契约 —— 所有切片依赖它是设计意图，不是越权。
#
# 技术债（记在案）：这些协议与 `hunter1/platform/*` 的实现同处一个包，
# 终态应把它们归位到各自的边界模块（如 Crawler 协议随抓取能力走），
# 届时本豁免一并删除。
SHARED_PORT_MODULE = "hunter1.application.ports"

# 切片间的**依赖方向白名单**：`{依赖方: (被依赖方, ...)}`，与 AGENTS.md 的
# 「依赖方向白名单（架构测试钉死，越权即红灯）：slices 之间只允许
#   crawl/scoring/applications/assistant → jobs」逐字对应。
#
# 为什么要有这一条：原先的三条守卫（不 import 组装根 / 不 import 未登记旧层 /
# 不深链别的切片）都不管**方向** —— `jobs → scoring`、`settings → crawl` 这类被
# 白名单禁止的依赖只要走公开面（`from hunter1.slices.scoring import ...`）就全绿，
# 而文档承诺的是「越权即红灯」。守卫缺口比现存违规更危险：它会静默地长回来。
SLICE_DEPENDENCY_ALLOW: dict[str, tuple[str, ...]] = {
    "applications": ("jobs",),
    "assistant": ("jobs",),
    "crawl": ("jobs",),
    "scoring": ("jobs",),
}


def _module_name(path: Path, root: Path = SRC) -> str:
    """文件的绝对模块名（如 `hunter1.slices.jobs.router`）。

    纯路径推导，不执行、不导入任何代码 —— 只用来给相对 import 定位基准包。
    """
    rel = path.resolve().relative_to(root.parent)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_relative(package: str, level: int) -> str | None:
    """把相对 import 的层级（`.` 的个数）还原成绝对包名；越出顶层时返回 None。"""
    parts = package.split(".") if package else []
    drop = level - 1
    if drop > len(parts):
        return None
    kept = parts[: len(parts) - drop] if drop else parts
    return ".".join(kept) or None


def _is_module_in_tree(dotted: str, root: Path) -> bool:
    """`hunter1.slices.jobs.store` 在源码树里是不是一个真实模块（或包）？

    用来分辨 `from hunter1.slices.jobs import store` 里的名字是**子模块**还是
    **公开面符号** —— AST 里两者长得一样，只有源码树能分辨。
    """
    parts = dotted.split(".")
    if not parts or parts[0] != root.name:
        return False
    path = root.joinpath(*parts[1:])
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


def _submodules_of(prefix: str, names: list[ast.alias], root: Path) -> set[str]:
    """`from <prefix> import a, b` 里那些**确实是子模块**的名字。

    ⚠️ 只看 `node.module`（前缀）是不够的：`from hunter1.slices.jobs import store`
    与 `from hunter1.slices.jobs.store import JobStore` 是同一件事 —— 都深链了
    切片的内部模块，但前者在 AST 里只是一条 `ImportFrom` 的 name。只收前缀会让
    它读起来像「走公开面」，从深链守卫下溜过去。

    反过来也不能无脑展开名字：`from hunter1.slices.jobs import JobStore` 是
    **合法**的公开面导入（AGENTS.md 明确允许），把 `JobStore` 也当成模块就会
    把合法写法误判成违规。判据只能是「这个名字在源码树里存在对应文件吗」。
    """
    found: set[str] = set()
    for alias in names:
        candidate = f"{prefix}.{alias.name}"
        if _is_module_in_tree(candidate, root):
            found.add(candidate)
    return found


def _imported_modules(path: Path, *, root: Path = SRC) -> set[str]:
    """收集一个文件 import 的全部模块（相对 import 统一还原成绝对名）。

    ⚠️ 相对 import 必须一并解析：`from ..jobs.store import JobStore` 与
    `from hunter1.slices.jobs.store import JobStore` 语义完全相同，但裸 AST 里
    前者 node.level=1、module="jobs.store"。只认 level==0 时，那个相对写法
    **完全绕过下面的分层与深链守卫** —— 「越权即红灯」这扇门就没锁
    （当前 src 里恰好没有相对 import，所以是潜伏漏洞而非现存违规）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = _module_name(path, root).rsplit(".", 1)[0]
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    modules.add(node.module)
                    modules.update(_submodules_of(node.module, node.names, root))
                continue
            base = _resolve_relative(package, node.level)
            if base is None:
                continue
            if node.module:
                qualified = f"{base}.{node.module}"
                modules.add(qualified)
                modules.update(_submodules_of(qualified, node.names, root))
            else:
                modules.add(base)
                modules.update(_submodules_of(base, node.names, root))
    return modules


def _deep_link_offender(path: Path, slice_name: str, *, root: Path = SRC) -> str | None:
    """该文件是否深链了**别的**切片的内部模块？返回第一个违规模块名。

    抽成函数是为了让它自己也能被测（见 TestDeepLinkRule）——
    守卫的判据若只活在断言里，「永远返回空」的实现可以一路绿下去。
    """
    for module in sorted(_imported_modules(path, root=root)):
        if not module.startswith("hunter1.slices."):
            continue
        parts = module.split(".")
        if len(parts) > 3 and parts[2] != slice_name:
            return module
    return None


def _py_files(layer: str) -> list[Path]:
    return sorted((SRC / layer).rglob("*.py"))


def _slice_dirs(root: Path = SRC) -> list[Path]:
    slices = root / "slices"
    return sorted(p for p in slices.iterdir() if p.is_dir() and p.name != "__pycache__")


def _rel(path: Path) -> str:
    return path.relative_to(SRC.parent.parent).as_posix()


@pytest.mark.parametrize("layer", sorted(FORBIDDEN_IMPORTS))
def test_layer_respects_import_boundaries(layer: str) -> None:
    offenders: list[str] = []
    for path in _py_files(layer):
        for module in _imported_modules(path):
            for banned in FORBIDDEN_IMPORTS[layer]:
                if module == banned or module.startswith(f"{banned}."):
                    offenders.append(f"{_rel(path)} imports {module}")
    assert not offenders, f"{layer} 分层违规：\n" + "\n".join(offenders)


def test_domain_uses_only_platform_text() -> None:
    """domain 对 platform 的依赖仅限 text（纯函数）—— 其余一律禁止。"""
    offenders: list[str] = []
    for path in _py_files("domain"):
        for module in _imported_modules(path):
            if module.startswith("hunter1.platform") and module != "hunter1.platform.text":
                offenders.append(f"{_rel(path)} imports {module}")
    assert not offenders, "domain 越权依赖 platform：\n" + "\n".join(offenders)


def test_domain_layer_has_no_third_party_io_libraries() -> None:
    """领域层不该直接依赖 HTTP / DB 客户端 —— 它是纯粹的。"""
    banned = {"httpx", "requests", "sqlalchemy", "bs4", "playwright"}
    offenders: list[str] = []
    for path in _py_files("domain"):
        for module in _imported_modules(path):
            root = module.split(".")[0]
            if root in banned:
                offenders.append(f"{_rel(path)} imports {module}")
    assert not offenders, "领域层出现了 IO 依赖：\n" + "\n".join(offenders)


def test_layers_exist() -> None:
    for layer in ["platform", "slices", "domain", "application"]:
        assert (SRC / layer).is_dir(), f"缺少分层目录: {layer}"


def test_tests_dir_mirrors_src() -> None:
    """`tests/` 的子目录必须镜像 `src/hunter1/`（AGENTS.md 的「镜像 src 结构」约定）。

    这条守卫来自两次真实的漂移：

    - `tests/infrastructure/` 有 10 个文件，但生产层的 `infrastructure/` 早在
      Wave 6 就被 `platform/` 取代 —— 目录名指向一个**已不存在的层**，
      新 Agent 照它找代码会扑空；
    - `tests/application/test_update.py` 测的其实是 `platform.update`，
      位置错了。

    名字错位的代价不在"不好看"：它让「测试放哪」需要靠记忆而非结构推导，
    而结构本来就能自证。
    """
    src_dirs = {p.name for p in SRC.iterdir() if p.is_dir() and p.name != "__pycache__"}
    tests_root = SRC.parent.parent / "tests"
    test_dirs = {p.name for p in tests_root.iterdir() if p.is_dir() and p.name != "__pycache__"}
    orphans = test_dirs - src_dirs
    assert not orphans, (
        f"tests/ 下的目录在 src/hunter1/ 没有对应物（层已改名或删除？）：{sorted(orphans)}"
    )


def test_application_layer_is_minimal() -> None:
    """`application/` 只允许两个模块 —— 并存期拷贝不得重新长出来。

    这里曾住着 `assistant / crawl / job_tools / tools / score` 五个模块，它们是
    `slices/` 对应实现的**逐字拷贝**（差异仅 import 前缀：2~4 行），生产代码 0 引用，
    却各带一套测试（105 项）。后果有两层：一是 ~840 行死代码会与 `slices/` **漂移**
    （改一边忘另一边，两套测试都绿、只有一套在生产跑）；二是那些「绿」不证明任何
    生产行为，是假信心。

    现存一个各有理由：
    - `ports.py`：共享的进程边界协议（见 SHARED_PORT_MODULE 的说明）；
    - `__init__.py`：包标识。

    `applications.py` 已随「投递」归属迁移删除：入口 `POST /api/applications`
    与记录本体同在 applications 切片，jobs 不再经旧层取 `new_application`，
    `SLICE_LEGACY_ALLOW` 随之清空。
    """
    allowed = {"__init__.py", "ports.py"}
    actual = {p.name for p in (SRC / "application").glob("*.py")}
    extra = actual - allowed
    assert not extra, f"application/ 出现了意料之外的模块（并存期拷贝？）：{sorted(extra)}"


def test_legacy_crawlers_layer_is_gone() -> None:
    """旧 `crawlers/` 层必须不存在（收尾迁移完成）：抓取能力归 `slices/crawl`。

    留着旧实现会让「站点注册表在哪」有两个答案 —— `cli.py` 就曾查旧表做参数
    校验，而应用查新表：新站点只登记到 `slices/crawl/sites.py` 时，CLI 会拒绝
    一个实际可用的站点。两套注册表在本次收尾前已并存，是真实的分叉风险。
    """
    assert not (SRC / "crawlers").exists(), "旧 crawlers/ 层残留"


def test_legacy_allowlist_is_empty() -> None:
    """过渡期白名单必须是空的（Wave 4 已收尾）。

    为什么在白名单守卫之外再加一条：`test_slices_respect_boundaries` 会**静默放行**
    登记过的条目 —— 登记制降低了迁移摩擦，代价是「谁往里加了东西」无声无息。
    这条把「加条目」变成一次红灯：真需要时得连这条断言一起改，至少被看见一次。
    """
    assert SLICE_LEGACY_ALLOW == {}, (
        "过渡期白名单非空 —— 新增条目请写明理由与清理期限，并同步这条断言"
    )


def test_assembly_root_exists() -> None:
    """组装根存在 —— 它是唯一认识所有切片的地方。"""
    assert (SRC / "main.py").is_file()


def test_legacy_ssr_layer_is_gone() -> None:
    """旧 SSR 层必须不存在（Wave 6 删除）：前后端完全分离后，
    留着旧渲染层会让「界面在哪实现」有两个答案。"""
    assert not (SRC / "web").exists(), "旧 web/ 层残留"


def test_slices_respect_boundaries() -> None:
    """切片不得 import 组装根；对旧层的依赖必须登记在册。

    登记制而不是全禁：迁移期允许**显式登记**的过渡依赖（可审计、有期限），
    比「悄悄放行」或「一律禁止导致迁移停摆」都更可控。
    """
    offenders: list[str] = []
    for slice_dir in _slice_dirs():
        allowed = SLICE_LEGACY_ALLOW.get(slice_dir.name, ())
        for path in sorted(slice_dir.rglob("*.py")):
            for module in _imported_modules(path):
                if module.startswith("hunter1.main"):
                    offenders.append(f"{_rel(path)} imports {module}")
                elif module == SHARED_PORT_MODULE:
                    continue  # 进程边界协议：见 SHARED_PORT_MODULE 的说明
                elif module.startswith("hunter1.application") and not any(
                    module == entry or module.startswith(f"{entry}.") for entry in allowed
                ):
                    offenders.append(f"{_rel(path)} imports {module}（未登记的旧层依赖）")
    assert not offenders, "切片边界违规：\n" + "\n".join(offenders)


def test_slices_do_not_reach_into_each_other() -> None:
    """切片间只经公开面 —— 禁止深链其他切片的内部模块。

    `from hunter1.slices.jobs import JobStore` 合法（公开面）；
    `from hunter1.slices.jobs.store import JobStore` 违规（深链内部）。
    `from hunter1.slices.jobs import store` 同样是深链 —— 名字指向的是内部模块，
    不是公开面符号（判据见 `_submodules_of`）。深链让领地边界失效，并行改动的
    冲突会从这里回来。
    """
    offenders: list[str] = []
    for slice_dir in _slice_dirs():
        name = slice_dir.name
        for path in sorted(slice_dir.rglob("*.py")):
            module = _deep_link_offender(path, name)
            if module is not None:
                offenders.append(f"{_rel(path)} imports {module}")
    assert not offenders, "切片深链违规：\n" + "\n".join(offenders)


def _slice_dependency_offenders(*, root: Path = SRC) -> list[str]:
    """切片之间出现白名单外的方向时，返回 `文件 import 模块` 列表。

    抽成函数（而不是只写在断言里）是为了让它自己也能被测试 —— 判据若只活在断言里，
    「永远返回空」的实现可以一路绿下去（见 `TestSliceDependencyRule`）。
    """
    offenders: list[str] = []
    for slice_dir in _slice_dirs(root):
        name = slice_dir.name
        allowed = set(SLICE_DEPENDENCY_ALLOW.get(name, ()))
        for path in sorted(slice_dir.rglob("*.py")):
            for module in sorted(_imported_modules(path, root=root)):
                parts = module.split(".")
                if len(parts) < 3 or parts[:2] != ["hunter1", "slices"]:
                    continue
                target = parts[2]
                if target == name or target in allowed:
                    continue
                where = path.relative_to(root).as_posix()
                offenders.append(f"{where} imports {module}（{name} → {target} 不在白名单）")
    return offenders


def test_slices_respect_dependency_direction() -> None:
    """切片间的依赖方向必须落在白名单里（AGENTS.md 的「越权即红灯」）。

    这条守卫原先**不存在**：三条旧守卫只管「不 import 组装根 / 不 import 未登记
    旧层 / 不深链别的切片」，方向本身没人管 —— `jobs → scoring` 或
    `settings → crawl` 只要走公开面就全绿，而文档承诺的是「架构测试钉死」。

    缺口比现存违规危险：现存依赖是合规的（唯一的跨切片 import 是
    `applications → jobs`），但没人拦着它长歪。
    """
    offenders = _slice_dependency_offenders()
    assert not offenders, "切片依赖方向违规：\n" + "\n".join(offenders)


def test_slice_legacy_allow_is_empty() -> None:
    """过渡期登记表必须保持为空 —— 加条目是「静默放行」，得连测试一起改。

    `SLICE_LEGACY_ALLOW` 是迁移期的存量清单（Wave 4 已清空）。留着字典本身是为了
    让「又冒出旧层依赖」时有一个**显式、可审计**的落点；但如果后人往里塞一条就
    静默放行，这个落点反而成了后门。这里把它钉成空表：真要加，必须同时改这条断言
    （也就必然进入 code review 的视线）。
    """
    assert SLICE_LEGACY_ALLOW == {}, (
        "过渡期白名单不该有内容（迁移已结束）。"
        "若确有存量依赖要放行，请连同 AGENTS.md 与这条断言一起评审修改。"
    )


def test_import_collection_is_not_silently_empty() -> None:
    """守卫自己不能「永远空」—— 收集器整体失效时，所有「offenders 为空」的断言
    都会照常通过（真实违规也就跟着隐形了）。

    这里钉一个必然成立的正面事实：真实文件确实收集到了 import。
    """
    collected = _imported_modules(SRC / "main.py")
    assert any(module.startswith("hunter1.") for module in collected)
    assert any(module.startswith("fastapi") for module in collected)


def test_release_source_port_matches_client_signature() -> None:
    """端口签名必须与实现一致 —— 实现不得多出端口里没有的隐藏参数。

    `@runtime_checkable` 的 isinstance 只检查方法存在、不检查签名，所以
    「实现多一个带默认值的参数」能悄悄存活：按端口写的测试替身/第三方实现
    会在某天以奇怪的方式失败。端口是唯一契约来源，参数集必须逐一对齐。
    """
    import inspect

    from hunter1.platform.update.client import ReleaseClient
    from hunter1.platform.update.ports import ReleaseSource

    for method in ("fetch_manifest", "download_asset"):
        port_signature = inspect.signature(getattr(ReleaseSource, method))
        impl_signature = inspect.signature(getattr(ReleaseClient, method))
        port_params = list(port_signature.parameters)
        impl_params = list(impl_signature.parameters)
        assert port_params == impl_params, (
            f"{method} 参数集不一致：端口 {port_params} vs 实现 {impl_params}"
        )


def _write_tree(
    tmp_path: Path, relative: str, source: str, *, modules: tuple[str, ...] = ()
) -> tuple[Path, Path]:
    """造一棵最小源码树：`relative` 处写 `source`，`modules` 处写空的模块文件。

    子模块识别的判据是「源码树里存在对应文件」（见 `_is_module_in_tree`），
    所以构造深链用例时必须把目标模块**真的建出来** —— 否则测的是一个空树，
    通过的结论对真实仓库没有意义。
    """
    root = tmp_path / "src" / "hunter1"
    for module in modules:
        extra = root.joinpath(*module.split(".")[1:]).with_suffix(".py")
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text("", encoding="utf-8")
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return root, target


class TestSliceDependencyRule:
    """方向判据本身要有**能变红**的用例（同 `TestDeepLinkRule` 的理由）。

    只断言「真实仓库里 offenders 为空」是不够的：收集逻辑整体失效（永远返回空集）
    时，那条断言照样绿 —— 而它的失效方式恰好就是「没人发现方向长歪了」。
    """

    def _offenders(
        self, tmp_path: Path, relative: str, source: str, modules: tuple[str, ...] = ()
    ) -> list[str]:
        root, _target = _write_tree(tmp_path, relative, source, modules=modules)
        return _slice_dependency_offenders(root=root)

    def test_whitelisted_direction_is_allowed(self, tmp_path: Path) -> None:
        """assistant → jobs 在白名单里（AGENTS.md 明确允许）。"""
        assert (
            self._offenders(
                tmp_path,
                "slices/assistant/router.py",
                "from hunter1.slices.jobs import JobSummary\n",
            )
            == []
        )

    def test_reverse_direction_is_flagged(self, tmp_path: Path) -> None:
        """jobs → scoring 是反向依赖：走公开面也不行。"""
        offenders = self._offenders(
            tmp_path, "slices/jobs/router.py", "from hunter1.slices.scoring import Score\n"
        )
        assert len(offenders) == 1
        assert "jobs → scoring 不在白名单" in offenders[0]

    def test_unlisted_pair_is_flagged(self, tmp_path: Path) -> None:
        """settings → crawl 不在任何白名单边里。"""
        offenders = self._offenders(
            tmp_path, "slices/settings/router.py", "from hunter1.slices.crawl import CrawlRunner\n"
        )
        assert len(offenders) == 1
        assert "settings → crawl 不在白名单" in offenders[0]

    def test_same_slice_import_is_not_flagged(self, tmp_path: Path) -> None:
        """切片内部的相对 import 不该被误判成跨切片。"""
        assert (
            self._offenders(tmp_path, "slices/crawl/service.py", "from . import adapters\n") == []
        )

    def test_allowed_direction_deep_link_is_left_to_the_deep_link_guard(
        self, tmp_path: Path
    ) -> None:
        """方向对、但深链 —— 方向守卫不管它（由深链守卫管），两把锁职责不重叠。"""
        offenders = self._offenders(
            tmp_path,
            "slices/applications/router.py",
            "from hunter1.slices.jobs.store import JobStore\n",
            modules=("hunter1.slices.jobs.store",),
        )
        assert offenders == []


class TestRelativeImportResolution:
    """相对 import 必须被还原成绝对模块名 —— 否则分层/深链守卫对它是瞎的。

    报告项 M6：`_imported_modules` 原先只收 `node.level == 0`，
    `from ..slices.jobs.store import X` 这类相对写法完全绕过检查。
    """

    def _mods(
        self, tmp_path: Path, relative: str, source: str, modules: tuple[str, ...] = ()
    ) -> set[str]:
        root, target = _write_tree(tmp_path, relative, source, modules=modules)
        return _imported_modules(target, root=root)

    def test_parent_relative_import_resolves_to_absolute(self, tmp_path: Path) -> None:
        """`..` 越级：切片深链另一个切片的内部模块。"""
        mods = self._mods(tmp_path, "slices/crawl/runner.py", "from ..jobs.store import JobStore\n")
        assert "hunter1.slices.jobs.store" in mods

    def test_sibling_relative_import_resolves_to_absolute(self, tmp_path: Path) -> None:
        """`.` 同级：切片内部模块。"""
        mods = self._mods(tmp_path, "slices/jobs/router.py", "from .store import JobStore\n")
        assert "hunter1.slices.jobs.store" in mods

    def test_from_dot_import_names_resolves_each(self, tmp_path: Path) -> None:
        """`from . import store` 的 node.module 为 None，需靠 names 还原。"""
        mods = self._mods(
            tmp_path,
            "slices/jobs/router.py",
            "from . import store\n",
            modules=("hunter1.slices.jobs.store",),
        )
        assert "hunter1.slices.jobs.store" in mods

    def test_relative_submodule_alias_resolves(self, tmp_path: Path) -> None:
        """`from ..jobs import store` —— name 是子模块，同样是深链。"""
        mods = self._mods(
            tmp_path,
            "slices/crawl/runner.py",
            "from ..jobs import store\n",
            modules=("hunter1.slices.jobs.store",),
        )
        assert "hunter1.slices.jobs.store" in mods

    def test_relative_beyond_top_returns_nothing(self, tmp_path: Path) -> None:
        """越出顶层的相对层级不应拼出畸形模块名。"""
        mods = self._mods(tmp_path, "x.py", "from ....nowhere import y\n")
        assert not any(m.endswith("nowhere.y") for m in mods)


class TestDeepLinkRule:
    """深链判据本身要有**能变红**的用例。

    报告项 M6 的另一半：原先所有守卫断言都是「offenders 为空」，收集逻辑整体
    失效（永远返回空集）时 874 个测试照样全绿 —— 守卫缺的是负向用例。
    这里同时钉住两个方向：真深链必须被认出，公开面导入不许误伤。
    """

    def _offender(
        self, tmp_path: Path, relative: str, source: str, modules: tuple[str, ...] = ()
    ) -> str | None:
        root, target = _write_tree(tmp_path, relative, source, modules=modules)
        # relative 形如 `slices/<切片名>/<文件>`：第二段就是切片名（与真实守卫传
        # `slice_dir.name` 一致）
        return _deep_link_offender(target, relative.split("/")[1], root=root)

    def test_flags_submodule_alias_in_absolute_from_import(self, tmp_path: Path) -> None:
        """`from hunter1.slices.jobs import store` → 深链（名字是内部模块）。"""
        offender = self._offender(
            tmp_path,
            "slices/crawl/runner.py",
            "from hunter1.slices.jobs import store\n",
            modules=("hunter1.slices.jobs.store",),
        )
        assert offender == "hunter1.slices.jobs.store"

    def test_flags_submodule_alias_in_relative_from_import(self, tmp_path: Path) -> None:
        offender = self._offender(
            tmp_path,
            "slices/crawl/runner.py",
            "from ..jobs import store\n",
            modules=("hunter1.slices.jobs.store",),
        )
        assert offender == "hunter1.slices.jobs.store"

    def test_public_symbol_import_is_not_flagged(self, tmp_path: Path) -> None:
        """`from hunter1.slices.jobs import JobStore` 是 AGENTS.md 允许的公开面导入。"""
        offender = self._offender(
            tmp_path,
            "slices/crawl/runner.py",
            "from hunter1.slices.jobs import JobStore, find_job\n",
        )
        assert offender is None

    def test_same_slice_internal_import_is_not_flagged(self, tmp_path: Path) -> None:
        offender = self._offender(
            tmp_path,
            "slices/jobs/router.py",
            "from hunter1.slices.jobs.store import JobStore\n",
        )
        assert offender is None
