"""Build data/fish.json and data/waterbodies.json from the official RF4 record tables.

The absolute record tables list every fish species once, in every site language. Rows are joined across
languages by the fish icon id (e.g. ``alb_barbel``), which is the same on all sites.

    python tools/build_data.py

The sites answer the first request with a tiny JavaScript "loader" page that sets two cookies; we compute
the same values here (see ``solve_challenge``) so a plain HTTP client gets the real page.
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
SITES = {'en': 'https://rf4game.com/records/', 'de': 'https://rf4game.de/records/', 'ru': 'https://rf4game.ru/records/'}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) BiteMap-data-builder/1.0'


def _jhash(code):
    x, k = 123456789, 0
    for i in range(1677696):
        x = ((x + code) ^ (x + (x % 3) + (x % 17) + code) ^ i) % 16776960
        if x % 117 == 0:
            k = (k + 1) % 1111
    return k


def fetch(url, attempts=4):
    for i in range(attempts):
        try:
            return _fetch_once(url)
        except RuntimeError as e:
            print(f'  retry {i + 1}: {e}', flush=True)
            time.sleep(5 * (i + 1))
    raise RuntimeError(f'{url}: giving up')


def _fetch_once(url):
    s = requests.Session()
    s.headers['User-Agent'] = UA
    r = s.get(url, timeout=30)
    if 'records_subtable' not in r.text:
        p = s.cookies.get('__js_p_')
        if not p:
            raise RuntimeError(f'{url}: no challenge cookie and no data')
        code = int(p.split(',')[0])
        host = urllib.parse.urlparse(url).hostname
        s.cookies.set('__jhash_', str(_jhash(code)), domain=host, path='/')
        s.cookies.set('__jua_', urllib.parse.quote(UA, safe=''), domain=host, path='/')
        time.sleep(1.5)  # like the browser's loader page
        r = s.get(url, timeout=30)
    if 'records_subtable' not in r.text:
        raise RuntimeError(f'{url}: still no record table')
    return r.text


ROW = re.compile(
    r'records_subtable.*?item_icon.*?/res/48x48/(?P<id>[^\'"/]+?)\.png.*?class="text"[^>]*>(?P<name>[^<]+)<'
    r'.*?class="col overflow nowrap weight"[^>]*>(?P<weight>[^<]+)<'
    r'.*?class="col overflow nowrap location"[^>]*>(?P<loc>[^<]+)<', re.S)


def parse_weight_g(s):
    s = html.unescape(s).replace('\xa0', ' ').strip()
    m = re.match(r'([\d\s.,]+)\s*(\S+)', s)
    if not m:
        return None
    num = float(m.group(1).replace(' ', '').replace(',', '.'))
    unit = m.group(2).lower()
    return round(num * 1000) if unit in ('kg', 'кг') else round(num)


def parse(page):
    out = {}
    for m in ROW.finditer(page):
        fid = m.group('id')
        if fid in out:
            continue
        out[fid] = {'name': html.unescape(m.group('name')).strip(),
                    'record_g': parse_weight_g(m.group('weight')),
                    'location': html.unescape(m.group('loc')).strip()}
    return out


def slug(s):
    return re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_')


def main():
    pages = {}
    for lang, url in SITES.items():
        print('fetching', url, flush=True)
        pages[lang] = parse(fetch(url))
        print(f'  {len(pages[lang])} species')
    ids = sorted(set(pages['en']) | set(pages['de']) | set(pages['ru']))
    fish, waters = [], {}
    for fid in ids:
        rows = {l: pages[l].get(fid) for l in SITES}
        en = rows['en'] or rows['de'] or rows['ru']
        fish.append({
            'id': fid,
            'names': {l: r['name'] for l, r in rows.items() if r},
            'record_g': max((r['record_g'] or 0) for r in rows.values() if r) or None,
            'record_water': slug(rows['en']['location']) if rows['en'] else None,
        })
        if rows['en']:
            wid = slug(rows['en']['location'])
            w = waters.setdefault(wid, {'id': wid, 'votes': {}})
            for l, r in rows.items():
                if r:
                    v = w['votes'].setdefault(l, {})
                    v[r['location']] = v.get(r['location'], 0) + 1
    os.makedirs(DATA, exist_ok=True)
    # keep hand-maintained map settings when the file already exists
    wpath = os.path.join(DATA, 'waterbodies.json')
    old = {}
    if os.path.exists(wpath):
        old = {w['id']: w for w in json.load(open(wpath, encoding='utf-8'))['waterbodies']}
    # records of one species can sit on different waterbodies in the regional tables, so the localised
    # name of a waterbody is the one most often paired with its English name
    for w in waters.values():
        w['names'] = {l: max(v.items(), key=lambda kv: kv[1])[0] for l, v in w.pop('votes').items()}
    ov = {}
    ov_path = os.path.join(DATA, 'overrides.json')
    if os.path.exists(ov_path):
        ov = json.load(open(ov_path, encoding='utf-8'))
    for wid, names in ov.get('waterbodies', {}).items():
        if wid in waters:
            waters[wid]['names'].update(names)
    for f in fish:
        f['names'].update(ov.get('fish', {}).get(f['id'], {}))
    merged = []
    for wid, w in sorted(waters.items()):
        o = old.get(wid, {})
        merged.append({**w, 'map': o.get('map'), 'coord_range': o.get('coord_range', [0, 999])})
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import build_trophies
    for p in build_trophies.merge(fish, merged):   # trophy weights + where each fish lives (data/trophies.tsv)
        print('!', p)
    with open(os.path.join(DATA, 'fish.json'), 'w', encoding='utf-8') as f:
        json.dump({'source': 'official RF4 absolute record tables', 'fish': fish}, f, ensure_ascii=False, indent=1)
    with open(wpath, 'w', encoding='utf-8') as f:
        json.dump({'waterbodies': merged}, f, ensure_ascii=False, indent=1)
    print(f'{len(fish)} fish, {len(merged)} waterbodies written to {DATA}')


if __name__ == '__main__':
    sys.exit(main())
