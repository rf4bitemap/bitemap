"""Player position from the HUD: the "73:48" under the compass in the bottom-right corner."""
import re
import time

import cv2
import numpy as np

from .. import ocr

# region relative to the bottom-right corner, in units of the client-area height
REGION = (0.14, 0.088, 0.105, 0.05)   # (from right, from bottom, width, height)
_RX = re.compile(r'(-?\d{1,4})\s*[:;.,]\s*(-?\d{1,4})')


def region(width, height):
    fr, fb, w, h = REGION
    x = int(width - fr * height)
    y = int(height - fb * height)
    return max(0, x), max(0, y), int(w * height), int(h * height)


def _digits_bbox(gray):
    """Box around the bright, digit-sized blobs on one line (skips the compass arrow and ring)."""
    h = gray.shape[0]
    # the HUD dims at night: threshold relative to the brightest pixels, not a fixed level
    thr = float(np.clip(np.percentile(gray, 99.5) - 40, 140, 205))
    _, bw = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
    n, _, st, _ = cv2.connectedComponentsWithStats(bw, 8)
    blobs = [s for s in st[1:] if 0.12 * h <= s[3] <= 0.6 * h and s[2] <= 0.8 * s[3] * 3 and s[4] >= 6]
    if not blobs:
        return None
    # the text line: blobs whose vertical centre is close to the median centre
    cy = sorted(s[1] + s[3] / 2 for s in blobs)[len(blobs) // 2]
    line = [s for s in blobs if abs(s[1] + s[3] / 2 - cy) <= 0.25 * h]
    x0 = min(s[0] for s in line); x1 = max(s[0] + s[2] for s in line)
    y0 = min(s[1] for s in line); y1 = max(s[1] + s[3] for s in line)
    pad = max(2, (y1 - y0) // 3)
    padx = max(3, y1 - y0)  # thin digits (1, 7) can break into fragments smaller than the blob filter
    return max(0, x0 - padx), max(0, y0 - pad), min(gray.shape[1], x1 + padx), min(h, y1 + pad)


def read_coords(img_bgr):
    """img: BGR crop of region(). Returns (x, y) or None."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    bb = _digits_bbox(gray)
    if bb:
        gray = gray[bb[1]:bb[3], bb[0]:bb[2]]
    for prepared in (ocr.prepare(gray, min_height=48), ocr.prepare(gray, min_height=72)):
        t = ocr.read_line(prepared, ('en',), whitelist='0123456789:-', psm=7)
        m = _RX.search(t)
        if m:
            x, y = int(m.group(1)), int(m.group(2))
            if -100 <= x <= 1100 and -100 <= y <= 1100:
                return x, y
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
