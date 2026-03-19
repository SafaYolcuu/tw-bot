# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# PyQt5 ve QtWebEngine icin gerekli veri dosyalari
datas = collect_data_files('PyQt5.QtWebEngineWidgets', include_py_files=False)
datas += collect_data_files('PyQt5.Qt5', include_py_files=False)

# PyQt5 alt modullerini topla
hiddenimports = collect_submodules('PyQt5')

a = Analysis(
    ['tribal_wars_bot.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', '_tkinter', 'matplotlib', 'numpy', 'pandas',
        'scipy', 'PIL', 'setuptools', 'pkg_resources',
    ],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TribalWarsBot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TribalWarsBot',
)
