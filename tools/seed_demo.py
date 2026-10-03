"""Fill a local BiteMap server with plausible fake catches (for trying the website / development only).

    python tools/seed_demo.py http://127.0.0.1:8000 [n]
"""
import json
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def shore_spots(m, k, rnd):
    """k random shoreline points of a map (land pixels right next to the blue-green water)."""
    import cv2
    import numpy as np
    img = cv2.imread(os.path.join(ROOT, 'data', 'maps', m['image']))
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    water = ((hsv[..., 0] >= 40) & (hsv[..., 0] <= 70) & (hsv[..., 1] > 30)).astype(np.uint8)
    water = cv2.morphologyEx(water, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    edge = cv2.dilate(water, np.ones((9, 9), np.uint8)) - water
    ys, xs = np.nonzero(edge)
    H, W = water.shape
    out = []
    if not len(xs):  # map colours we don't recognise: anywhere in the middle of the map
        cx, cy = (m['min_x'] + m['max_x']) / 2, (m['min_y'] + m['max_y']) / 2
        r = (m['max_x'] - m['min_x']) / 4
        return [(round(cx + rnd.uniform(-r, r)), round(cy + rnd.uniform(-r, r))) for _ in range(k)]
    for _ in range(k):
        i = rnd.randrange(len(xs))
        x = m['min_x'] + xs[i] / W * (m['max_x'] - m['min_x'])
        y = m['max_y'] - ys[i] / H * (m['max_y'] - m['min_y'])
        out.append((round(x), round(y)))
    return out


def main():
    url = sys.argv[1].rstrip('/') if len(sys.argv) > 1 else 'http://127.0.0.1:8000'
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
    fish = json.load(open(os.path.join(ROOT, 'data', 'fish.json'), encoding='utf-8'))['fish']
    by_water = {}
    for f in fish:
        by_water.setdefault(f['record_water'], []).append(f)
    random.seed(4)
    installs = []
    for _ in range(8):
        r = requests.post(url + '/api/v1/installs', json={'client_version': 'demo'})
        r.raise_for_status()
        installs.append({'Authorization': 'Bearer ' + r.json()['token']})
    batches = {i: [] for i in range(len(installs))}
    wb = {w['id']: w for w in json.load(open(os.path.join(ROOT, 'data', 'waterbodies.json'), encoding='utf-8'))['waterbodies']}
    waters = [w for w in by_water if w and (wb.get(w) or {}).get('map')]
    hot = {w: shore_spots(wb[w]['map'], 7, random.Random(i)) for i, w in enumerate(waters)}
    for _ in range(n):
        water = random.choice(waters[:8])
        hx, hy = random.choice(hot[water])
        f = random.choice(by_water[water])
        rec = f['record_g'] or 5000
        w = max(10, int(random.triangular(0.05, 0.9, 0.25) * rec))
        batches[random.randrange(len(installs))].append({
            'uuid': str(uuid.uuid4()),
            'caught_at': (datetime.now(timezone.utc) - timedelta(minutes=random.randint(1, 60 * 24 * 25))).isoformat(),
            'fish_id': f['id'], 'waterbody': water, 'x': hx + random.randint(-1, 1), 'y': hy + random.randint(-1, 1),
            'weight_g': w, 'length_cm': round(20 + 60 * w / rec, 1), 'badge': 'trophy' if w > 0.7 * rec else None,
            'coord_age': 5.0})
    for i, cs in batches.items():
        for k in range(0, len(cs), 100):
            r = requests.post(url + '/api/v1/catches', json={'client_version': 'demo', 'catches': cs[k:k + 100]},
                              headers=installs[i])
            r.raise_for_status()
    print(f'seeded {n} demo catches on {len(waters[:8])} waterbodies')


if __name__ == '__main__':
    main()
