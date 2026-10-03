"""The catch card: fish name on top, then [weight] [length] [trophy label] pills, keepnet/release buttons below.

Detection is language independent: we look for the weight icon with the ruler icon to its right on the same row.
Then we read the name above that row and the numbers inside the two pills. The trophy label isn't read: trophy and
super trophy follow from fish + weight (GameData.trophy_level).
"""
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import cv2
import numpy as np

from .. import ocr
from . import load_template, match_multiscale, template_base_h

# every OCR call is its own tesseract.exe (~0.2 s, mostly start-up), so name, weight and length run side by side
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='ocr')

TEMPLATE_BASE_H = 1125          # client-area height the icon templates were cut at
SCALE_STEPS = (0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4)
WEIGHT_THRESHOLD = 0.80   # real cards score 0.88-0.98, foliage false positives ~0.68
RULER_THRESHOLD = 0.70

# Where the card's weight/length row sits, measured on real cards at 720p-4K (in units of the client height H):
# row at 0.14 H from the top, weight icon 0.12 H left of the centre, ruler 0.03 H left of it, icons 5.5 icon
# heights apart. The tolerances allow other UI scales, longer weights ("1 234,567 kg") and cards without a badge.
ROW_Y = (0.08, 0.22)            # row centre / H
WEIGHT_LEFT_OF_CENTRE = (0.02, 0.26)
RULER_FROM_CENTRE = (-0.16, 0.14)
ICON_GAP = (3.0, 10.5)          # (ruler x - weight x) / weight icon height



@dataclass
class CardGeometry:
    row_y: int        # centre line of the weight/length row (frame coordinates)
    icon_h: int
    weight_box: tuple  # x, y, w, h of the weight icon
    ruler_box: tuple
    score: float


@dataclass
class CardReading:
    name_text: str
    fish_id: str
    fish_score: float
    lang: str
    weight_g: int
    weight_text: str
    length_cm: float
    length_text: str


class CatchCardDetector:
    def __init__(self):
        self.weight_tpl = load_template('weight_icon.png')
        self.ruler_tpl = load_template('ruler_icon.png')
        self.base_h = template_base_h('weight_icon.png', TEMPLATE_BASE_H)
        self._last_scale = None
        self._last_h = None

    # ------------------------------------------------------------------ geometry
    def find(self, frame, client_h=None):
        """frame: BGR image of the client area (or its top band; then pass the full client height).
        Returns CardGeometry or None."""
        if self.weight_tpl is None or self.ruler_tpl is None:
            return None
        W = frame.shape[1]
        H = client_h or frame.shape[0]
        # search only where the card's row can be (centred horizontally, upper part of the screen)
        x0 = max(0, int(W / 2 - 0.36 * H))
        x1 = min(W, int(W / 2 + 0.30 * H))
        y0 = int(H * (ROW_Y[0] - 0.04))
        y1 = min(frame.shape[0], int(H * (ROW_Y[1] + 0.04)))
        if y1 - y0 < 20 or x1 - x0 < 40:
            return None
        band = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        base = H / self.base_h
        scales = [base * s for s in SCALE_STEPS]
        if self._last_h != H:
            self._last_scale, self._last_h = None, H
        w = match_multiscale(band, self.weight_tpl, [self._last_scale], 0.85) if self._last_scale else None
        if w is None:
            w = match_multiscale(band, self.weight_tpl, scales, WEIGHT_THRESHOLD)
        if w is None:
            return None
        score, wx, wy, ww, wh, scale = w
        icon_h = max(6, int(round(wh * 0.75)))
        # ruler: same row, to the right, within ~14 icon heights
        ry0 = max(0, wy - wh)
        ry1 = min(band.shape[0], wy + 2 * wh)
        rx0 = wx + ww
        rx1 = min(band.shape[1], wx + ww + int(16 * wh))
        sub = band[ry0:ry1, rx0:rx1]
        r = match_multiscale(sub, self.ruler_tpl, [scale * f for f in (0.9, 1.0, 1.1)], RULER_THRESHOLD)
        if r is None:
            return None
        rscore, rx, ry, rw, rh, _ = r
        g = CardGeometry(
            row_y=y0 + wy + wh // 2,
            icon_h=icon_h,
            weight_box=(x0 + wx, y0 + wy, ww, wh),
            ruler_box=(x0 + rx0 + rx, y0 + ry0 + ry, rw, rh),
            score=min(score, rscore),
        )
        if not self._plausible(frame, g, W, H):
            return None
        self._last_scale = scale
        return g

    @staticmethod
    def _plausible(frame, g, W, H):
        """Reject matches that don't sit where and how a real catch card's row does.
        (Small cards have small pills, so the background around the icons can't be used as a check.)"""
        wx, wy, ww, wh = g.weight_box
        rx, ry, rw, rh = g.ruler_box
        if not ROW_Y[0] <= g.row_y / H <= ROW_Y[1]:
            return False
        if not WEIGHT_LEFT_OF_CENTRE[0] <= (W / 2 - wx) / H <= WEIGHT_LEFT_OF_CENTRE[1]:
            return False
        if not RULER_FROM_CENTRE[0] <= (rx - W / 2) / H <= RULER_FROM_CENTRE[1]:
            return False
        if not ICON_GAP[0] <= (rx - wx) / max(1, wh) <= ICON_GAP[1]:
            return False
        if abs((ry + rh / 2) - (wy + wh / 2)) > 0.6 * wh:
            return False
        # the fish name: large bright text above the row
        name = CatchCardDetector._name_region(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), g)
        bb = ocr.text_bbox(name, thresh=200, pad=0) if name.size else None
        return bool(bb) and (bb[3] - bb[1]) >= 0.8 * g.icon_h

    @staticmethod
    def _name_region(gray, g, below=-1.6):
        """Above the weight/length row and centred on it: the fish name (with below=1: down to the pills too)."""
        W, ih = gray.shape[1], g.icon_h
        cx = (g.weight_box[0] + g.ruler_box[0] + g.ruler_box[2]) // 2
        return gray[max(0, g.row_y - int(7.5 * ih)):max(1, g.row_y + int(below * ih)),
                    max(0, cx - int(0.3 * W)):min(W, cx + int(0.3 * W))]

    def text_region(self, frame, g):
        """Grayscale of the name and the pills - stops changing once the card has finished fading in."""
        return self._name_region(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), g, below=1)

    @staticmethod
    def text_change(a, b):
        """How much the bright parts (text) differ between two text_region()s, in gray levels."""
        if a.shape != b.shape:
            return 255.0
        a, b = a.astype(np.int16), b.astype(np.int16)
        bright = np.maximum(a, b) > 150
        if not bright.any():
            return 255.0
        return float(np.abs(a - b)[bright].mean())

    # ------------------------------------------------------------------ reading
    def read(self, frame, g, gamedata, langs, water=None):
        H, W = frame.shape[:2]  # frame may be just the top band; all crops are relative to its top-left
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        ih = g.icon_h
        wx, wy, ww, wh = g.weight_box
        rx, ry, rw, rh = g.ruler_box

        # --- weight / length pills: text between the icon and the end of the pill's dark background
        py0, py1 = max(0, g.row_y - ih), min(H, g.row_y + ih)
        w_end = min(self._pill_end(gray, wx + ww, g.row_y, ih), rx - int(0.8 * ih))
        l_end = self._pill_end(gray, rx + rw, g.row_y, ih)
        weight_job = _POOL.submit(self._read_weight, gray, wx + ww, w_end, py0, py1)
        length_job = _POOL.submit(self._read_pill, gray, rx + rw, l_end, py0, py1, '0123456789.,cmсм ')

        # --- fish name: big white text centred above the row (meanwhile the pills are being read)
        name_img = self._name_region(gray, g)
        bb = ocr.text_bbox(name_img, thresh=200, pad=6)
        name_text, fish_id, fish_score, lang = '', None, 0, None
        if bb:
            crop = name_img[bb[1]:bb[3], bb[0]:bb[2]]
            prepared = ocr.prepare(crop, min_height=48)

            def name_in(l):
                t = ocr.read_line(prepared, (l,), psm=7)
                return (t,) + gamedata.match_fish(t, langs=(l,), water=water)[:2]
            # the most likely language first (the last one that worked); the others only if that fails
            results = [(langs[0], name_in(langs[0]))] if langs else []
            if langs and not results[0][1][1]:
                jobs = [(l, _POOL.submit(name_in, l)) for l in langs[1:]]
                results += [(l, j.result()) for l, j in jobs]
            for l, (t, fid, score) in results:
                if score > fish_score:
                    name_text, fish_score = t, score
                if fid:
                    name_text, fish_id, fish_score, lang = t, fid, score, l
                    break

        wtxt, weight_g = weight_job.result()
        ltxt = length_job.result()
        length_cm = parse_length(ltxt)
        return CardReading(name_text, fish_id, fish_score, lang, weight_g, wtxt, length_cm, ltxt)

    @staticmethod
    def _pill_end(gray, x_start, row_y, ih):
        """x where the dark pill background ends, scanning right along a line just above the text."""
        y = max(0, row_y - int(0.85 * ih))
        line = gray[y, :].astype(int)
        ref = int(np.median(line[x_start:x_start + max(2, ih // 2)]))
        miss, x, limit = 0, x_start, min(gray.shape[1] - 1, x_start + 14 * ih)
        while x < limit:
            if abs(line[x] - ref) > 18:
                miss += 1
                if miss >= max(2, ih // 5):
                    return x - miss
            else:
                miss = 0
            x += 1
        return limit

    @staticmethod
    def _read_pill(gray, x0, x1, y0, y1, whitelist):
        if x1 - x0 < 8:
            return ''
        crop = gray[y0:y1, x0:x1]
        bb = ocr.text_bbox(crop, thresh=160, pad=3)
        if bb:
            crop = crop[bb[1]:bb[3], bb[0]:bb[2]]
        return ocr.read_line(ocr.prepare(crop, min_height=56), ('en', 'ru'), whitelist=whitelist, psm=7)

    @classmethod
    def _read_weight(cls, gray, x0, x1, y0, y1):
        """'6,722 kg' / '68 g': OCR only the number; tell g from kg by the unit's width.

        OCR on the whole text confuses a lone 'g' with a '9' ('68 g' -> '689'), so the unit is split off at the
        widest gap and never read as text. 'g'/'г' is about half a digit-height wide, 'kg'/'кг' about one.
        """
        if x1 - x0 < 8:
            return '', None
        crop = gray[y0:y1, x0:x1]
        bb = ocr.text_bbox(crop, thresh=160, pad=0)
        if not bb:
            return '', None
        crop = crop[bb[1]:bb[3], bb[0]:bb[2]]
        h = crop.shape[0]
        cols = (crop > 160).any(axis=0)
        # runs of empty columns between glyphs
        gaps, start = [], None
        for i, filled in enumerate(cols):
            if not filled and start is None:
                start = i
            elif filled and start is not None:
                gaps.append((i - start, start, i))
                start = None
        split = max(gaps) if gaps else None
        if not split or split[0] < max(2, h * 0.18):
            # no clear space before the unit: fall back to reading the whole thing
            txt = cls._read_pill(gray, x0, x1, y0, y1, '0123456789.,kgKGкг ')
            return txt, parse_weight(txt)
        number, unit = crop[:, :split[1]], crop[:, split[2]:]
        unit_cols = np.nonzero((unit > 160).any(axis=0))[0]
        unit_w = (unit_cols[-1] - unit_cols[0] + 1) if len(unit_cols) else 0
        is_kg = unit_w > 0.8 * h
        # OCR needs some background around the digits: re-cut the number from the pill with a margin
        m = max(2, h // 4)
        nx0, nx1 = x0 + bb[0], x0 + bb[0] + split[1]
        ny0, ny1 = y0 + bb[1], y0 + bb[3]
        number = gray[max(0, ny0 - m):ny1 + m, max(0, nx0 - m):nx1 + max(1, m // 2)]
        num = ocr.read_line(ocr.prepare(number, min_height=64), ('en',), whitelist='0123456789.,', psm=7)
        txt = f"{num} {'kg' if is_kg else 'g'}"
        return txt, parse_weight(txt)


# ---------------------------------------------------------------------- parsing
_NUM_UNIT = re.compile(r'(\d[\d\s.,]*)\s*([a-zA-Zа-яА-Я]*)')


def parse_weight(text):
    """'6,722 kg' / '6.722kg' / '722 g' / '6722 kg' (lost separator) -> grams (int) or None."""
    t = (text or '').replace(' ', '')
    m = _NUM_UNIT.search(t)
    if not m:
        return None
    num, unit = m.group(1).strip(), m.group(2).lower()
    digits = re.sub(r'\D', '', num)
    if not digits:
        return None
    has_sep = bool(re.search(r'[.,]', num))
    is_kg = unit.startswith(('k', 'к')) or (not unit and has_sep)
    if is_kg:
        if has_sep:
            whole, frac = re.split(r'[.,]', num, maxsplit=1)
            frac = re.sub(r'\D', '', frac)[:3].ljust(3, '0')
            whole = re.sub(r'\D', '', whole) or '0'
            return int(whole) * 1000 + int(frac)
        # separator lost by OCR: RF4 always shows three decimals for kg
        return int(digits) if len(digits) > 3 else int(digits) * 1000
    return int(digits)


def parse_length(text):
    m = re.search(r'(\d+(?:[.,]\d+)?)', (text or '').replace(' ', ''))
    if not m:
        return None
    v = float(m.group(1).replace(',', '.'))
    return v if 0 < v < 1000 else None
