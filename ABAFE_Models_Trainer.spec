# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['src/gui.py'],
    pathex=['/workspaces/RolemIAster_Model_Trainer/.venv/lib/python3.12/site-packages', '/workspaces/RolemIAster_Model_Trainer/src'],
    binaries=[],
    datas=[('src/i18n', 'src/i18n'), ('README_UI.md', '.')],
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
    name='ABAFE_Models_Trainer',
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
    name='ABAFE_Models_Trainer',
)
