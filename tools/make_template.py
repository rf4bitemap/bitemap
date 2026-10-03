"""Cut a detector template out of a full-resolution game screenshot.

    python tools/make_template.py SCREENSHOT.png NAME x y w h [--pad 3]

NAME is one of: bite_icon, weight_icon, ruler_icon. x/y/w/h is the icon's box in screenshot pixels
(e.g. read it off in Paint). Use a native, uncompressed screenshot (PNG) of the game at any resolution.
The template is written to client/bitemap_logger/assets/NAME.png and the screenshot's height is recorded in
assets/templates.json so the detectors can rescale it for other resolutions.
For bite_icon, dark background pixels around the icon are made transparent (used as a match mask).
"""
import argparse
import json
import os

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, 'client', 'bitemap_logger', 'assets')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('screenshot')
    ap.add_argument('name', choices=['bite_icon', 'weight_icon', 'ruler_icon'])
    ap.add_argument('x', type=int)
    ap.add_argument('y', type=int)
    ap.add_argument('w', type=int)
    ap.add_argument('h', type=int)
    ap.add_argument('--pad', type=int, default=3)
    a = ap.parse_args()
    img = cv2.imdecode(np.fromfile(a.screenshot, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit('cannot read ' + a.screenshot)
    p = a.pad
    crop = img[max(0, a.y - p):a.y + a.h + p, max(0, a.x - p):a.x + a.w + p]
    out = os.path.join(ASSETS, a.name + '.png')
    if a.name == 'bite_icon':
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))
        rgba = cv2.cvtColor(crop, cv2.COLOR_BGR2BGRA)
        rgba[:, :, 3] = mask
        cv2.imencode('.png', rgba)[1].tofile(out)
    else:
        cv2.imencode('.png', cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY))[1].tofile(out)
    meta_path = os.path.join(ASSETS, 'templates.json')
    meta = json.load(open(meta_path, encoding='utf-8')) if os.path.exists(meta_path) else {}
    meta[a.name + '.png'] = {'base_h': img.shape[0], 'source': os.path.basename(a.screenshot)}
    json.dump(meta, open(meta_path, 'w', encoding='utf-8'), indent=1)
    print(f'wrote {out} ({crop.shape[1]}x{crop.shape[0]}), base height {img.shape[0]}')


if __name__ == '__main__':
    main()
