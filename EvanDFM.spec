# -*- mode: python ; coding: utf-8 -*-
"""Evan DFM — PyInstaller 打包配置 (单文件 exe)。

打包:  build.bat   或   pyinstaller EvanDFM.spec --noconfirm
产物:  dist\\DFM.exe

说明:
  * rules/ 与 assets/ 作为数据文件打入包内 (只读出厂配置);
    用户自定义规则放 %APPDATA%\\EvanDFM\\rules, 优先级更高且升级不丢失。
  * plugins/ 只打包示例 (以 _ 开头, 默认不加载), 用户插件放
    %APPDATA%\\EvanDFM\\plugins 或 exe 同级 plugins 目录。
"""
import os

block_cipher = None
ROOT = os.path.abspath(".")

datas = [
    (os.path.join(ROOT, "rules"), "rules"),
    (os.path.join(ROOT, "assets"), "assets"),
    (os.path.join(ROOT, "plugins"), "plugins"),
]

hiddenimports = [
    # 检查项靠 import 副作用注册, 打包器无法静态推断 → 必须显式列出
    "dfm.checks.capability",
    "dfm.checks.fabrication",
    "dfm.checks.assembly",
    "dfm.checks.netlist",
    "dfm.checks.fabdata",
    "dfm.checks.fabnet",
    "dfm.checks.panelization",
    "dfm.checks.netclass",
    # 几何内核
    "dfm.geom.board",
    "dfm.geom.shapes",
    "dfm.geom.elements",
    "dfm.geom.polarity",
    "dfm.geom.clearance",
    "dfm.geom.measure",
    # 解析器
    "dfm.io.gerber",
    "dfm.io.excellon",
    "dfm.io.odbpp",
    "dfm.io.aperture",
    "dfm.io.extracta",
    "dfm.io.skill_export",
    # 报告
    "dfm.report.snapshot",
    "dfm.report.svg",
    "dfm.report.html",
    "dfm.report.writers",
    # 第三方
    "yaml",
    "openpyxl",
    "PIL",
    "shapely",
    "shapely.geometry",
    "shapely.ops",
    "shapely.affinity",
    "shapely.strtree",
]

a = Analysis(
    ["evan_dfm.py"],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "scipy", "pandas", "numpy.testing",
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtWebEngineCore",
        "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia", "PySide6.QtBluetooth", "PySide6.QtNfc",
        "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
        "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtWebSockets",
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="DFM",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                 # GUI 程序; 需要看命令行输出时改为 True
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "assets", "evan.ico"),
    version=None,
    uac_admin=False,
    manifest=os.path.join(ROOT, "Evan_DFM.manifest"),
)
