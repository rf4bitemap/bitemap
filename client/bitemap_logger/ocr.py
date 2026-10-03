"""Thin Tesseract wrapper. Tesseract (Apache-2.0) ships next to the exe in release builds."""
import logging
import os
import shutil
import subprocess

import cv2
import numpy as np
import pytesseract

from .paths import TESSERACT_DIRS

log = logging.getLogger(__name__)
TESS_LANG = {'en': 'eng', 'de': 'deu', 'ru': 'rus'}
_ready = None


def setup():
    """Locate tesseract.exe. Returns True when OCR is available."""
    global _ready
    if _ready is not None:
        return _ready
    exe = os.environ.get('BITEMAP_TESSERACT')
    if not exe:
        for d in TESSERACT_DIRS:
            if os.path.exists(os.path.join(d, 'tesseract.exe')):
                exe = os.path.join(d, 'tesseract.exe')
                break
    exe = exe or shutil.which('tesseract')
    if not exe:
        log.error('Tesseract not found')
        _ready = False
        return False
    pytesseract.pytesseract.tesseract_cmd = exe
    tessdata = os.path.join(os.path.dirname(exe), 'tessdata')
    if os.path.isdir(tessdata):
        os.environ['TESSDATA_PREFIX'] = tessdata
    _ready = True
    log.info('Tesseract: %s', exe)
    return True


def available_langs():
    if not setup():
        return []
    try:
        return pytesseract.get_languages(config='')
    except Exception:
        return []


def _no_window():
    # pytesseract spawns tesseract.exe; keep it from flashing a console window in the GUI build
    if os.name == 'nt' and not getattr(subprocess.Popen, '_bitemap', False):
        orig = subprocess.Popen

        class Popen(orig):
            _bitemap = True

            def __init__(self, *a, **kw):
                kw.setdefault('creationflags', 0x08000000)  # CREATE_NO_WINDOW
                super().__init__(*a, **kw)
        subprocess.Popen = Popen


_no_window()


def prepare(gray, min_height=40, invert=True):
    """Upscale small text and binarise (white text on dark UI -> black on white for Tesseract)."""
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    h = gray.shape[0]
    if h < min_height:
        f = min_height / max(h, 1)
        gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(gray, 0, 255, (cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY) + cv2.THRESH_OTSU)
    return cv2.copyMakeBorder(bw, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=255)


def read_line(img, langs=('en',), whitelist=None, psm=7):
    if not setup():
        return ''
    lang = '+'.join(TESS_LANG.get(l, l) for l in langs)
    cfg = f'--psm {psm} --oem 1'
    if whitelist:
        cfg += f' -c tessedit_char_whitelist={whitelist}'
    try:
        return pytesseract.image_to_string(img, lang=lang, config=cfg).strip()
    except Exception as e:
        log.warning('OCR failed: %s', e)
        return ''


def text_bbox(gray, thresh=170, pad=4):
    """Bounding box of bright text pixels -> (x0, y0, x1, y1) or None."""
    mask = (gray > thresh).astype(np.uint8)
    if mask.sum() < 20:
        return None
    ys, xs = np.nonzero(mask)
    h, w = gray.shape[:2]
    return (max(0, xs.min() - pad), max(0, ys.min() - pad), min(w, xs.max() + 1 + pad), min(h, ys.max() + 1 + pad))
