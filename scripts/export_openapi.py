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
    from hunter1.web.app import create_app
    from hunter1.web.context import AppContext

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
    )

    paths = len(spec.get("paths", {}))  # type: ignore[arg-type]
    schemas = len(spec.get("components", {}).get("schemas", {}))  # type: ignore[union-attr]
    print(f"已导出：{out}（{paths} 个路径 / {schemas} 个 schema）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
