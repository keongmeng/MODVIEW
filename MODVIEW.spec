# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：python -m PyInstaller MODVIEW.spec"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = []
hiddenimports = [
    "mod_scene",
    "mod_scene.node",
    "mod_scene.mod_scene",
    "mod_scene.descriptors",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "OpenGL",
    "OpenGL.GL",
    "OpenGL.arrays",
    "OpenGL.platform",
    "OpenGL.raw",
]
datas += collect_data_files("OpenGL")
datas += collect_data_files("tkinter")
hiddenimports += collect_submodules("OpenGL")

a = Analysis(
    ["modview.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MODVIEW",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
)
