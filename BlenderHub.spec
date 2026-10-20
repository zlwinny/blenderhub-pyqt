# -*- mode: python ; coding: utf-8 -*-
"""BlenderHub 绿色版打包配置。

默认产出 onedir 便携版（dist/BlenderHub/），整目录拷贝即用、启动快。
设置环境变量 BLENDERHUB_ONEFILE=1 时产出单文件 exe（启动需解压，稍慢）。

构建：pyinstaller BlenderHub.spec --noconfirm --clean
"""
import os

block_cipher = None

_onefile = os.environ.get("BLENDERHUB_ONEFILE") == "1"

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("assets/fonts", "assets/fonts"), ("assets/icon.ico", ".")],
    hiddenimports=["PyQt6.QtSvg", "requests", "bs4", "send2trash"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt6.QtQml",
        "PyQt6.QtQuick",
        "PyQt6.Qt3DCore",
        "PyQt6.Qt3DRender",
        "PyQt6.Qt3DInput",
        "PyQt6.Qt3DAnimation",
        "PyQt6.Qt3DLogic",
        "PyQt6.QtMultimedia",
        "PyQt6.QtMultimediaWidgets",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtPdf",
        "PyQt6.QtPdfWidgets",
        "PyQt6.QtDataVisualization",
        "PyQt6.QtCharts",
        "PyQt6.QtBluetooth",
        "PyQt6.QtSerialPort",
        "PyQt6.QtTest",
        "PyQt6.QtDesigner",
        "PyQt6.QtHelp",
        "PyQt6.QtSql",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    *([] if not _onefile else [a.binaries, a.zipfiles, a.datas]),
    exclude_binaries=not _onefile,
    name="BlenderHub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if not _onefile:
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="BlenderHub",
    )
