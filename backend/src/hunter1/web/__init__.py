"""接口层：本地 Web UI（FastAPI）与 CLI。

只做「翻译」——把 HTTP/命令行输入转成应用层用例调用，不含业务规则。
"""

from __future__ import annotations

from hunter1.web.app import create_app
from hunter1.web.context import AppContext

__all__ = ["AppContext", "create_app"]
