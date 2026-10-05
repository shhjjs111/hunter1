# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— `python scripts/build.py` 会用它。

改之前先想清楚的三点：

1. **入口是 `src/hunter1/__main__.py`**：它调 `cli.main()`，与安装后的
   `hunter1` 命令走同一条路径，不存在「打包版行为不一样」。
2. **templates 必须显式带上**：`web/app.py` 用 `Path(__file__).parent / "templates"`
   定位模板，而 `.py` 被编译进归档后不会顺带带上同目录的 `.html`。
   目标路径 `hunter1/web/templates` 与源码结构保持一致 —— 于是同一行代码在
   开发态与打包态都能解析到。
3. **`pathex=['src']`**：src 布局下包不在仓库根，得告诉分析器去哪找。

产物是**单目录**模式（`dist/hunter1/`：一个 exe + `_internal/`），实测约 42MB。
不用单文件模式：它每次启动都要把内容解压到临时目录，启动慢，且更容易被
防病毒软件误报。
"""

a = Analysis(
    ['src/hunter1/__main__.py'],
    pathex=['src'],
    binaries=[],
    datas=[('src/hunter1/web/templates', 'hunter1/web/templates')],
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
