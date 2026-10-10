"""最小端到端示例：领域模型 → SQLite 持久化 → 读回。

这是 M1 数据层的「可摸到」验证——不依赖网络与模型，直接跑：

    ./.tools/python/python.exe examples/demo_persist.py

演示两件事：
1. 岗位标题的归一化投影（`title_key`）在入库与读回后保持一致；
2. 数据真实落在磁盘上的 SQLite 文件里（不是内存态）。
"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

# 允许直接 `python examples/demo_persist.py` 而无需安装（与 pytest 的 pythonpath 一致）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from hunter1.domain.models import CaptureStatus, Company, Job
from hunter1.platform.db import Database


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="hunter1-demo-")
    db_path = Path(tmp) / "hunter1-demo.db"
    db = Database(db_path)
    db.initialize()
    print(f"数据库文件: {db_path}")

    db.companies().upsert(Company(id="c1", name="示例科技", source="demo"))

    raw_title = "AI产品经理（2027校招）"
    job = Job(
        id="j1",
        company_id="c1",
        title=raw_title,
        detail_url="https://example.com/job/1",
        source="demo",
        city="北京",
        capture_status=CaptureStatus.COMPLETE,
        first_seen_at=datetime(2026, 9, 1, tzinfo=UTC),
        last_seen_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    db.jobs().upsert(job)
    print(f"入库岗位标题: {raw_title!r}")

    loaded = db.jobs().get("j1")
    assert loaded is not None, "岗位应能读回"

    print(f"读回岗位标题: {loaded.title!r}")
    print(f"归一化 title_key: {loaded.title_key!r}")
    print(f"公司数: {db.companies().count()}  岗位数: {db.jobs().count()}")

    # 先把库关掉再校验/清理——SQLite 在 Windows 上会占用文件句柄
    db.dispose()

    if loaded.title_key != "ai产品经理":
        print(f"[FAIL] 归一化结果不符：期望 'ai产品经理'，实际 {loaded.title_key!r}")
        return 1
    if loaded != job:
        print("[FAIL] 往返后模型不相等（有字段丢失或被改写）")
        return 1
    print("[OK] 归一化正确，且往返后模型完全相等")

    # 实测文件确实落在磁盘上（存在且有内容）。
    # 先判存在再 stat：文件没落盘时 `db_path.stat()` 会抛 FileNotFoundError，
    # 把「校验没通过」变成一次崩溃 —— 校验脚本该给出结论，不是堆栈。
    exists = db_path.is_file()
    size = db_path.stat().st_size if exists else 0
    on_disk = exists and size > 0
    print(f"[OK] 数据已落盘: {on_disk}（{size} 字节）")
    if not on_disk:
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
