# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— `python scripts/build.py` 会用它。

改之前先想清楚的三点：

1. **入口是 `src/hunter1/__main__.py`**：它调 `cli.main()`，与安装后的
   `hunter1` 命令走同一条路径，不存在「打包版行为不一样」。
2. **前端构建产物必须显式带上**：`main.py` 在打包态用
   `sys._MEIPASS / "hunter1" / "web_dist"` 定位它（见 `main.frontend_dir()`）。
   `.py` 被编译进归档时不会顺带带上 `frontend/dist`，所以必须在 datas 里声明；
   目标路径与解析分支保持一致 —— 于是同一行代码在开发态与打包态都成立。
   （Wave 6 之前这里是 `web/templates`；SSR 层删除后换成前端产物。）
3. **`pathex=['src']`**：src 布局下包不在仓库根，得告诉分析器去哪找。

产物是**单目录**模式（`dist/hunter1/`：一个 exe + `_internal/`），实测约 43MB。
不用单文件模式：它每次启动都要把内容解压到临时目录，启动慢，且更容易被
防病毒软件误报。
"""

import os
from pathlib import Path

# scripts/build.py 会以 cwd=backend 调用；允许环境变量覆盖以便独立调试
_BACKEND = Path(os.environ.get("HUNTER1_BACKEND_DIR") or Path.cwd())
_FRONTEND_DIST = Path(
    os.environ.get("HUNTER1_FRONTEND_DIST") or (_BACKEND.parent / "frontend" / "dist")
)

datas = []
if _FRONTEND_DIST.is_dir():
    # 目标 "hunter1/web_dist" 与 main.frontend_dir() 的打包态分支一致
    datas.append((str(_FRONTEND_DIST), "hunter1/web_dist"))

a = Analysis(
    ['src/hunter1/__main__.py'],
    pathex=['src'],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='hunter1',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='hunter1',
)
