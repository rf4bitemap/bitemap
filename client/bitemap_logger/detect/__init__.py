"""Screen detectors. All of them take a BGR image of the game's client area (or a region of it)."""
import json
import os

import cv2
import numpy as np

from ..paths import ASSETS


def template_base_h(name, default):
    """Client-area height the template was cut at (assets/templates.json, written by tools/make_template.py)."""
    try:
        with open(os.path.join(ASSETS, 'templates.json'), encoding='utf-8') as f:
            return json.load(f).get(name, {}).get('base_h', default)
    except (OSError, ValueError):
        return default


def load_template(name):
    p = os.path.join(ASSETS, name)
    if not os.path.exists(p):
        return None
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def match_multiscale(gray, tpl, scales, threshold):
    """Best TM_CCOEFF_NORMED match over scales -> (score, x, y, w, h, scale) or None."""
    best = None
    for s in scales:
        t = cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        th, tw = t.shape[:2]
        if th < 6 or tw < 6 or th >= gray.shape[0] or tw >= gray.shape[1]:
            continue
        res = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if best is None or mx > best[0]:
            best = (float(mx), loc[0], loc[1], tw, th, s)
    if best and best[0] >= threshold:
        return best
    return None
