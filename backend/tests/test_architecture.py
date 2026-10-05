"""架构约束的自动化守卫。

把「分层」从口头约定变成**可执行断言** —— 否则它会在某次赶工里悄悄破掉。

规则（迁移期版本；终态随各波次收紧，见 AGENTS.md）：

- `platform`（机制内核）不得 import `hunter1.{slices, application, web, crawlers}`。
  过渡期豁免：允许依赖 `hunter1.domain.*`（共享模型 —— Wave 4 后随各切片归位，
  届时本豁免删除）。
- `domain`（纯模型与规则）不得 import `hunter1.{application, web, crawlers, slices}`；
  对 platform 只允许 `platform.text`（纯函数）。
- `application`（用例）不得 import `hunter1.{platform, web, crawlers, slices}` ——
  只依赖 domain 与端口（Protocol）。
- `slices`（业务切片）不得 import `hunter1.{main, crawlers}`；对旧层
  （`hunter1.application`）的依赖必须登记在 `SLICE_LEGACY_ALLOW`（Wave 4 清空）；
  切片之间只经公开面（`__init__`），不得深链其他切片的内部模块。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "hunter1"

# 每层禁止的顶层包前缀（迁移期规则；见模块 docstring）
FORBIDDEN_IMPORTS: dict[str, tuple[str, ...]] = {
    "platform": ("hunter1.slices", "hunter1.application", "hunter1.crawlers", "hunter1.main"),
    "domain": ("hunter1.application", "hunter1.crawlers", "hunter1.slices", "hunter1.main"),
    "application": (
        "hunter1.platform",
        "hunter1.crawlers",
        "hunter1.slices",
        "hunter1.main",
    ),
}

# 切片 → 过渡期登记的旧层依赖（Wave 4 完成后必须清空；新增条目需评审）
SLICE_LEGACY_ALLOW: dict[str, tuple[str, ...]] = {
    # 投递记录本体的归属是 applications 切片；jobs 只做入口，Wave 4 切换
    "jobs": ("hunter1.application.applications",),
}

# 协议层的共享豁免：`application/ports.py` 里是**进程边界**的抽象
# （Crawler / JobRepository / TextFetcher / LLMProvider），它们不是「某个切片的实现」，
# 而是切片与外部世界之间的契约 —— 所有切片依赖它是设计意图，不是越权。
#
# 技术债（记在案）：这些协议与 `hunter1/platform/*` 的实现同处一个包，
# 终态应把它们归位到各自的边界模块（如 Crawler 协议随抓取能力走），
# 届时本豁免一并删除。
SHARED_PORT_MODULE = "hunter1.application.ports"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def _py_files(layer: str) -> list[Path]:
    return sorted((SRC / layer).rglob("*.py"))


def _slice_dirs() -> list[Path]:
    slices = SRC / "slices"
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
    for layer in ["platform", "slices", "domain", "application", "crawlers"]:
        assert (SRC / layer).is_dir(), f"缺少分层目录: {layer}"


def test_assembly_root_exists() -> None:
    """组装根存在 —— 它是唯一认识所有切片的地方。"""
    assert (SRC / "main.py").is_file()


def test_legacy_ssr_layer_is_gone() -> None:
    """旧 SSR 层必须不存在（Wave 6 删除）：前后端完全分离后，
    留着旧渲染层会让「界面在哪实现」有两个答案。"""
    assert not (SRC / "web").exists(), "旧 web/ 层残留"


def test_slices_respect_boundaries() -> None:
    """切片不得 import web / crawlers；对旧层的依赖必须登记在册。

    登记制而不是全禁：迁移期允许**显式登记**的过渡依赖（可审计、有期限），
    比「悄悄放行」或「一律禁止导致迁移停摆」都更可控。
    """
    offenders: list[str] = []
    for slice_dir in _slice_dirs():
        allowed = SLICE_LEGACY_ALLOW.get(slice_dir.name, ())
        for path in sorted(slice_dir.rglob("*.py")):
            for module in _imported_modules(path):
                if module.startswith(("hunter1.main", "hunter1.crawlers")):
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
    深链让领地边界失效，并行改动的冲突会从这里回来。
    """
    offenders: list[str] = []
    for slice_dir in _slice_dirs():
        name = slice_dir.name
        for path in sorted(slice_dir.rglob("*.py")):
            for module in _imported_modules(path):
                if module.startswith("hunter1.slices."):
                    parts = module.split(".")
                    if len(parts) > 3 and parts[2] != name:
                        offenders.append(f"{_rel(path)} imports {module}")
    assert not offenders, "切片深链违规：\n" + "\n".join(offenders)


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
