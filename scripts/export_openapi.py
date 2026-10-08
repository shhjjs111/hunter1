"""导出 OpenAPI 契约快照 —— 从**真实组装根**生成，保证契约与运行时同源。

    ./.tools/python/python.exe scripts/export_openapi.py [--out 路径]

默认输出到 `contracts/openapi.json`。用临时目录建一个空库：导出只读路由表，
不触发任何业务写入、不碰用户数据。

序列化刻意 `sort_keys=True`：让「契约有没有变」的 diff 干净可审 —— 键序抖动
不该进入漂移判据。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))


def build_spec() -> dict[str, object]:
    from hunter1.main import AppContext, create_app

    # 不再需要「占位画像」那类补丁：评分的端点**始终挂载**，画像未配置只是运行时的
    # 状态（调用时给 409），不影响路由表。契约因此与运行时**按构造一致** ——
    # 而不是靠导出时塞一个假数据去凑出一份「比现实更好看」的接口清单。
    with tempfile.TemporaryDirectory(prefix="hunter1-openapi-") as tmp:
        context = AppContext.default(db_path=Path(tmp) / "openapi.db", site_keys=[])
        try:
            app = create_app(context)
            return app.openapi()
        finally:
            context.db.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="export_openapi.py", description="导出 OpenAPI 快照")
    parser.add_argument("--out", default="", help="输出路径（默认 contracts/openapi.json）")
    args = parser.parse_args(argv)

    out = Path(args.out) if args.out else ROOT / "contracts" / "openapi.json"
    spec = build_spec()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        # 行尾固定成 LF：`.gitattributes` 声明 `* text=auto eol=lf`，而 Python 在
        # Windows 上默认把换行翻成 CRLF —— 快照在**工作树**里是 CRLF、在 git blob
        # 里是 LF。于是 fresh checkout（LF）后的第一次 `contracts.sh --check` 会拿
        # LF 快照去比 CRLF 生成物 → 判成「漂移」，而它给的修复动作（重跑导出）写出的
        # 仍是 CRLF、提交时被 git 归一化掉 —— 本机红、CI 绿，且按提示修不掉。
        # 判据是内容等价，行尾就不该由平台决定。
        newline="\n",
    )

    paths = len(spec.get("paths", {}))  # type: ignore[arg-type]
    schemas = len(spec.get("components", {}).get("schemas", {}))  # type: ignore[union-attr]
    print(f"已导出：{out}（{paths} 个路径 / {schemas} 个 schema）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
