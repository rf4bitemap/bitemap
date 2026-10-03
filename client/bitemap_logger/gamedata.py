"""Fish and waterbody lists (data/*.json) and fuzzy matching of OCR text against fish names."""
import json
import os
import re
import unicodedata

from rapidfuzz import fuzz

from .paths import GAME_DATA

LANGS = ('en', 'de', 'ru')


def norm(s):
    """Upper-case, strip accents, ß->SS, Ё->Е, keep letters/digits/spaces. Works for Latin and Cyrillic."""
    s = str(s or '').replace('ß', 'ss').replace('ẞ', 'SS').replace('ё', 'е').replace('Ё', 'Е')
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'[^\w ]+|_', ' ', s).upper()
    return re.sub(r'\s+', ' ', s).strip()


class GameData:
    def __init__(self, data_dir=GAME_DATA):
        with open(os.path.join(data_dir, 'fish.json'), encoding='utf-8') as f:
            self.fish = {x['id']: x for x in json.load(f)['fish']}
        with open(os.path.join(data_dir, 'waterbodies.json'), encoding='utf-8') as f:
            self.waterbodies = {x['id']: x for x in json.load(f)['waterbodies']}
        # normalised name -> fish id, per language
        self._names = {lang: [(norm(x['names'][lang]), fid) for fid, x in self.fish.items() if lang in x['names']]
                       for lang in LANGS}

    def fish_name(self, fish_id, lang):
        f = self.fish.get(fish_id)
        if not f:
            return fish_id or '?'
        return f['names'].get(lang) or f['names'].get('en') or fish_id

    def water_name(self, water_id, lang):
        w = self.waterbodies.get(water_id)
        if not w:
            return water_id or ''
        return w['names'].get(lang) or w['names'].get('en') or water_id

    def match_fish(self, text, langs=LANGS, threshold=80):
        """Best fish for an OCR'd name -> (fish_id, score, lang) or (None, best_score, None).

        Long names win ties so that e.g. "Spiegelkarpfen" beats "Karpfen" and "Карп зеркальный" beats "Карп".
        """
        t = norm(text)
        if len(t) < 3:
            return None, 0, None
        best = (None, 0.0, None, 0)
        for lang in langs:
            for n, fid in self._names.get(lang, ()):
                if abs(len(n) - len(t)) > max(4, len(n) // 2):
                    continue
                score = fuzz.ratio(t, n)
                if score > best[1] or (score == best[1] and len(n) > best[3]):
                    best = (fid, score, lang, len(n))
        if best[1] >= threshold:
            return best[0], best[1], best[2]
        return None, best[1], None

    def plausible_weight(self, fish_id, weight_g):
        f = self.fish.get(fish_id)
        if not f or not weight_g:
            return False
        rec = f.get('record_g') or 0
        return 0 < weight_g <= max(rec * 1.25, rec + 500) if rec else weight_g > 0
