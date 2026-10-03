"""Player position from the HUD: the "73:48" in the compass/minimap in the bottom-right corner.

The compass can sit at slightly different places (HUD scale, bought maps make it a minimap with a textured
background), so instead of reading a fixed box we search the corner for a short line of white, digit-sized
glyphs and read only that line from a clean black-on-white mask (no map texture behind it).
"""
import re
import time

import cv2
import numpy as np

from .. import ocr

# search area relative to the bottom-right corner, in units of the client-area height
REGION = (0.34, 0.36, 0.34, 0.36)   # (from right, from bottom, width, height)
_RX = re.compile(r'(-?\d{1,4})\s*[:;.,-]\s*(-?\d{1,4})')   # the colon is sometimes read as '-'


def region(width, height):
    fr, fb, w, h = REGION
    x = int(width - fr * height)
    y = int(height - fb * height)
    return max(0, x), max(0, y), int(w * height), int(h * height)


def _whiteness(img_bgr):
    """High for bright uncoloured pixels (the HUD text), low for beige map paper, grass, coloured markers."""
    p = img_bgr.astype(np.int16)
    mn = p.min(axis=2)
    return np.clip(mn - (p.max(axis=2) - mn), 0, 255).astype(np.uint8)


def _threshold(whiteness):
    # adapts to the brightest white in the area (the HUD dims at night)
    return float(np.clip(np.percentile(whiteness, 99.95) - 60, 130, 195))   # text is a tiny part of the area


def _white_mask(img_bgr):
    w = _whiteness(img_bgr)
    return (w >= _threshold(w)).astype(np.uint8) * 255


def _text_lines(mask, ref_h):
    """Text lines of digit-height blobs -> list of (x0, y0, x1, y1, n_blobs).

    Neighbouring digits often merge into one blob ("73" in small HUDs), so a line is judged by its overall
    width relative to its height ("41:57" is ~3-5 heights wide), not by counting glyphs.
    """
    n, _, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    blobs = [s for s in st[1:] if 0.005 * ref_h <= s[3] <= 0.03 * ref_h and s[2] <= 4 * s[3] and s[4] >= 4]
    blobs.sort(key=lambda s: s[0])
    lines, used = [], set()
    for i, a in enumerate(blobs):
        if i in used:
            continue
        line, last = [a], a
        cy = a[1] + a[3] / 2
        for j in range(i + 1, len(blobs)):
            b = blobs[j]
            if j in used or abs(b[1] + b[3] / 2 - cy) > 0.5 * a[3] or not 0.5 * a[3] <= b[3] <= 1.6 * a[3]:
                continue
            if b[0] - (last[0] + last[2]) > 1.6 * a[3]:
                break
            line.append(b)
            used.add(j)
            last = b
        x0 = min(s[0] for s in line)
        y0 = min(s[1] for s in line)
        x1 = max(s[0] + s[2] for s in line)
        y1 = max(s[1] + s[3] for s in line)
        h = max(1, y1 - y0)
        if 1.8 <= (x1 - x0) / h <= 9.5 and len(line) <= 10:   # "7:5" .. "1036:1036"
            lines.append((x0, y0, x1, y1, len(line)))
    return lines


def _variants(img_bgr, box):
    """The line as black-on-white images, upscaled *before* thresholding so small digits keep their shape.

    Main: a thin cut of the whiteness at 80 % / 70 % of the brightest text pixel - only the white text survives,
    whatever is behind it (map paper, dark buildings on the minimap, grey compass). Otsu on grayscale is only a
    tie-breaker: on the minimap it splits dark map features from light paper instead of text from background.
    """
    x0, y0, x1, y1, _ = box
    h = y1 - y0
    mx, my = max(3, int(1.3 * h)), max(3, h // 2)   # generous sides: thin edge digits can be missing from the box
    crop = img_bgr[max(0, y0 - my):y1 + my, max(0, x0 - mx):x1 + mx]
    scale = max(1.0, 80 / max(h, 1))
    big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    w = _whiteness(big)
    hi = float(np.percentile(w, 99.5))
    yield np.where(w >= 0.8 * hi, 0, 255).astype(np.uint8)
    yield np.where(w >= 0.7 * hi, 0, 255).astype(np.uint8)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
    yield cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]


def _read_line(img_bgr, box):
    """Two thin cuts that agree win; otherwise the tie-breaker decides; otherwise the main cut alone
    (the tracker still needs two identical readings in a row before it changes the spot)."""
    seen = []
    for img in _variants(img_bgr, box):
        xy = _parse(ocr.read_line(img, ('en',), whitelist='0123456789:-', psm=7))
        if xy and xy in seen:
            return xy
        seen.append(xy)
    return seen[0]


def _parse(t):
    m = _RX.search(t or '')
    if m:
        x, y = int(m.group(1)), int(m.group(2))
        if -100 <= x <= 1100 and -100 <= y <= 1100:
            return x, y
    return None


def read_coords(img_bgr, ref_h=None):
    """img: BGR crop of region() (or any crop containing the coordinates). ref_h: client height (for glyph size).
    Returns (x, y) or None."""
    ref_h = ref_h or max(img_bgr.shape[0] / REGION[3], 1)
    mask = _white_mask(img_bgr)
    lines = _text_lines(mask, ref_h)
    # the coordinates are the lowest text in the compass; the keepnet counter ("8/100") sits above it
    lines.sort(key=lambda b: -b[3])
    for box in lines[:3]:
        xy = _read_line(img_bgr, box)
        if xy:
            return xy
    return None


class CoordTracker:
    """Keeps the last confirmed position. A new value must be seen twice in a row (OCR noise filter)."""

    def __init__(self):
        self.value = None
        self.at = 0.0
        self._pending = None

    def feed(self, xy, now=None):
        now = now or time.time()
        if xy is None:
            return
        if xy == self.value:
            self.at = now
            self._pending = None
        elif xy == self._pending:
            self.value, self.at, self._pending = xy, now, None
        else:
            self._pending = xy

    def get(self, max_age=600, now=None):
        now = now or time.time()
        if self.value and now - self.at <= max_age:
            return self.value, now - self.at
        return None, None
