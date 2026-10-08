"""生成物的行尾不能由平台决定。

`.gitattributes` 声明 `* text=auto eol=lf` —— 入库内容一律 LF。于是任何**生成后会入库**
的文件（契约快照、前端类型）在工作树里也必须是 LF，否则：

- fresh checkout（LF）之后跑 `contracts.sh --check`，`diff -q` 拿 LF 快照比 CRLF 生成物
  → 判成「契约漂移」；
- 而它给出的修复动作（重跑导出）写出的仍是 CRLF，提交时被 git 归一化掉 ——
  本机红、CI 绿，且按提示**修不掉**。

发现路径：审查报告 P1-4（实测当日 `contracts/openapi.json`、`main.py`、`AGENTS.md` 在
工作树里都是 CRLF，git blob 都是 LF）。修法是给写文件处显式 `newline="\\n"`，本文件
把这条约束钉住 —— 光靠「记得写 newline」会在下一个人手里丢。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: 生成后**会入库**的产物：它们必须与 git 里的形态一致（LF）
COMMITTED_ARTIFACTS = (
    ROOT / "contracts" / "openapi.json",
    ROOT / "frontend" / "src" / "shared" / "api" / "schema.d.ts",
)


def _load_exporter() -> ModuleType:
    """按路径加载 scripts/export_openapi.py（scripts/ 不是包，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location(
        "export_openapi_script", ROOT / "scripts" / "export_openapi.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("artifact", COMMITTED_ARTIFACTS, ids=lambda p: p.name)
def test_committed_artifact_is_lf_only(artifact: Path) -> None:
    """入库产物在工作树里不得出现 CRLF。"""
    if not artifact.is_file():
        pytest.skip(f"产物不存在：{artifact}")
    raw = artifact.read_bytes()
    assert b"\r\n" not in raw, (
        f"{artifact.name} 含 CRLF —— 与 .gitattributes 的 eol=lf 不一致，"
        "会让契约漂移判定「本机红、CI 绿」。重新生成时请确认写入处带 newline='\\n'。"
    )
    assert raw.endswith(b"\n"), "生成物应以单个换行收尾（diff 干净）"


def test_contract_snapshot_matches_the_exporter_output(tmp_path: Path) -> None:
    """重新导出的字节**必须与入库快照逐字节相同**（含行尾）。

    这条不只是行尾守卫：它同时证明「快照确实是这个导出器产出的」——手改过的快照、
    或导出器换了序列化参数而忘了重导，都会在这里红（与 `contracts.sh --check` 同一判据，
    但**不依赖平台**：两边都是本项目解释器写出来的）。
    """
    committed = ROOT / "contracts" / "openapi.json"
    if not committed.is_file():
        pytest.skip("契约快照不存在")

    exporter = _load_exporter()
    out = tmp_path / "openapi.json"
    assert exporter.main(["--out", str(out)]) == 0

    raw = out.read_bytes()
    assert b"\r\n" not in raw, "导出器写出的快照含 CRLF（newline= 没生效）"
    assert raw == committed.read_bytes(), "重新导出的快照与入库快照不一致（需重跑 contracts.sh）"


def test_exporter_is_runnable_as_a_script(tmp_path: Path) -> None:
    """当脚本跑一遍也要产出 LF —— 测试里 import 的路径与 CLI 路径一致。"""
    out = tmp_path / "cli.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_openapi.py"), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert b"\r\n" not in out.read_bytes()
