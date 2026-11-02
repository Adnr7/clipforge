# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build definition for the Windows ClipForge desktop app."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


SPEC_LOCATION = Path(SPECPATH).resolve()
ROOT = next(
    candidate
    for candidate in (SPEC_LOCATION, SPEC_LOCATION.parent, SPEC_LOCATION.parent.parent)
    if (candidate / 'frontend').is_dir()
)

datas = [
    (str(ROOT / 'frontend' / 'dist'), 'frontend/dist'),
    *collect_data_files('yt_dlp'),
]
hiddenimports = [
    'yt_dlp',
    *collect_submodules('yt_dlp'),
]


a = Analysis(
    [str(ROOT / 'main.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    a.binaries,
    a.datas,
    [],
    name='ClipForge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / 'assets' / 'clipforge.ico'),
)
