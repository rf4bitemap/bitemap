# PyInstaller spec for BiteMap Logger (build with: python tools/build_client.py)
import os
from PyInstaller.utils.hooks import collect_data_files

HERE = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(HERE, 'bitemap_logger')

datas = [
    (os.path.join(PKG, 'assets'), 'assets'),
    (os.path.join(PKG, 'locales'), 'locales'),
    (os.path.join(ROOT, 'data', 'fish.json'), 'data'),
    (os.path.join(ROOT, 'data', 'waterbodies.json'), 'data'),
    (os.path.join(ROOT, 'LICENSE'), '.'),
] + collect_data_files('customtkinter')

a = Analysis(
    [os.path.join(HERE, 'run_logger.py')],
    pathex=[HERE],
    datas=datas,
    hiddenimports=['bitemap_logger'],
    excludes=['matplotlib', 'pandas', 'scipy', 'PyQt5', 'PySide6', 'IPython', 'pytest'],
)
# Tesseract goes in as plain data (a Tree) so PyInstaller doesn't re-collect its DLLs next to Python's
a.datas += Tree(os.path.join(HERE, 'vendor', 'tesseract'), prefix='tesseract')
# OpenCV's video I/O backend (ffmpeg, ~30 MB) is never used
a.binaries = [b for b in a.binaries if 'opencv_videoio_ffmpeg' not in b[0]]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='BiteMapLogger',
    icon=os.path.join(PKG, 'assets', 'app.ico'),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name='BiteMapLogger', upx=False)
