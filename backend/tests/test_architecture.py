"""架构约束的自动化守卫。

把「分层」从口头约定变成**可执行断言** —— 否则它会在某次赶工里悄悄破掉。

规则（见 docs/DEVELOPMENT.md）：
- `domain` 不得 import `hunter1.infrastructure`（领域层不认识外部世界）
- `application` 不得 import `hunter1.infrastructure`（用例只依赖端口）
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "hunter1"


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


def _rel(path: Path) -> str:
    return path.relative_to(SRC.parent.parent).as_posix()


@pytest.mark.parametrize("layer", ["domain", "application"])
def test_layer_does_not_import_infrastructure(layer: str) -> None:
    offenders: list[str] = []
    for path in _py_files(layer):
        for module in _imported_modules(path):
            if module == "hunter1.infrastructure" or module.startswith("hunter1.infrastructure."):
                offenders.append(f"{_rel(path)} imports {module}")
    assert not offenders, "分层违规：\n" + "\n".join(offenders)


def test_domain_does_not_import_application() -> None:
    offenders: list[str] = []
    for path in _py_files("domain"):
        for module in _imported_modules(path):
            if module == "hunter1.application" or module.startswith("hunter1.application."):
                offenders.append(f"{_rel(path)} imports {module}")
    assert not offenders, "分层违规：\n" + "\n".join(offenders)


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
    for layer in ["domain", "application", "infrastructure", "web", "crawlers"]:
        assert (SRC / layer).is_dir(), f"缺少分层目录: {layer}"


def test_release_source_port_matches_client_signature() -> None:
    """端口签名必须与实现一致 —— 实现不得多出端口里没有的隐藏参数。

    `@runtime_checkable` 的 isinstance 只检查方法存在、不检查签名，所以
    「实现多一个带默认值的参数」能悄悄存活：按端口写的测试替身/第三方实现
    会在某天以奇怪的方式失败。端口是唯一契约来源，参数集必须逐一对齐。
    """
    import inspect

    from hunter1.application.ports import ReleaseSource
    from hunter1.infrastructure.update import ReleaseClient

    for method in ("fetch_manifest", "download_asset"):
        port_signature = inspect.signature(getattr(ReleaseSource, method))
        impl_signature = inspect.signature(getattr(ReleaseClient, method))
        port_params = list(port_signature.parameters)
        impl_params = list(impl_signature.parameters)
        assert port_params == impl_params, (
            f"{method} 参数集不一致：端口 {port_params} vs 实现 {impl_params}"
        )
