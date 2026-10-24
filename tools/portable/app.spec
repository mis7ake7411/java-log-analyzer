from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


portable_dir = Path(SPECPATH).resolve()
project_root = portable_dir.parents[1]
source_root = project_root / "src"

datas = [
    (str(path), "log_analyzer/tcss")
    for path in sorted((source_root / "log_analyzer" / "tcss").glob("*.tcss"))
]
datas += collect_data_files("textual")
datas += copy_metadata("java-log-analyzer")
datas += copy_metadata("textual")

a = Analysis(
    [str(portable_dir / "entry.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("textual.widgets"),
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
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="log-analyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="log-analyzer",
)
