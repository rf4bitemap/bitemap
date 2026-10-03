"""Bite alert: the round "fish" icon that appears at the left end of the line-tension bar when a fish bites.

The icon is pure white on whatever the game shows behind it, so we match a black/white version of it:
template and screen are both thresholded, which makes the match independent of the background.
Template: assets/bite_icon.png (cut with tools/make_template.py; its source height is in templates.json).
"""
import os
import time

import cv2
import numpy as np

from ..paths import ASSETS
from . import template_base_h

SCALE_STEPS = (0.85, 0.92, 1.0, 1.08, 1.16)
THRESHOLD = 0.60          # bite frames score 0.72-0.82, frames without a bite <= 0.40 (samples)
REARM_AFTER = 2.0     # seconds the icon must be gone before a new bite counts
WHITE = 200           # the icon's white is ~240 on any background


class BiteDetector:
    def __init__(self):
        self.tpl = None
        self.base_h = 1125
        p = os.path.join(ASSETS, 'bite_icon.png')
        if os.path.exists(p):
            img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            if img is not None:
                if img.ndim == 3 and img.shape[2] == 4:
                    img = img[:, :, :3]
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
                self.tpl = np.where(gray > WHITE, 255, 0).astype(np.uint8)
            self.base_h = template_base_h('bite_icon.png', 1125)
        self._present = False
        self._last_seen = 0.0
        self._scale = None
        self._h = None
        self.last_score = 0.0

    @property
    def enabled(self):
        return self.tpl is not None

    @staticmethod
    def region(width, height):
        """Left of the (centred) line-tension bar, bottom of the screen. Works for 16:9 and ultrawide."""
        x0 = int(width / 2 - 0.45 * height)
        x1 = int(width / 2 - 0.15 * height)
        y0 = int(height * 0.88)
        return max(0, x0), y0, max(1, x1 - max(0, x0)), height - y0

    def score(self, region_bgr, client_height):
        gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY)
        bw = np.where(gray > WHITE, 255, 0).astype(np.uint8)
        if bw.max() == 0:
            return 0.0
        if self._h != client_height:
            self._scale, self._h = None, client_height
        base = client_height / self.base_h
        scales = [self._scale] if self._scale else [base * s for s in SCALE_STEPS]
        best = (0.0, None)
        for s in scales:
            t = cv2.resize(self.tpl, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            t = np.where(t > 127, 255, 0).astype(np.uint8)
            if t.shape[0] >= bw.shape[0] or t.shape[1] >= bw.shape[1] or t.shape[0] < 8:
                continue
            v = float(cv2.matchTemplate(bw, t, cv2.TM_CCOEFF_NORMED).max())
            if v > best[0]:
                best = (v, s)
        if best[0] >= THRESHOLD:
            self._scale = best[1]
        elif self._scale and best[0] < 0.3:
            self._scale = None  # resolution/UI scale changed: search all scales again
        return best[0]

    def update(self, region_bgr, client_height, now=None):
        """Feed region(). Returns True exactly once per new bite."""
        if not self.enabled:
            return False
        now = now or time.time()
        self.last_score = self.score(region_bgr, client_height)
        seen = self.last_score >= THRESHOLD
        new_bite = seen and not self._present
        if seen:
            self._present, self._last_seen = True, now
        elif self._present and now - self._last_seen > REARM_AFTER:
            self._present = False
        return new_bite
