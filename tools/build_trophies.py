"""Add trophy / super trophy weights and the waterbodies each fish lives in to data/fish.json.

Source: data/trophies.tsv (fish, trophy weight, super trophy weight, found at - English names as in the game).

    python tools/build_trophies.py

Adds per fish: trophy_g, super_trophy_g (null: the fish has no super trophy) and waters (waterbody ids).
Fish missing from the table keep none of these (no trophy levels, found anywhere). build_data.py runs this too.
"""
import csv
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
# names the table uses for waterbodies whose official English name is longer
WATER_ALIASES = {'Amber Lake': 'the_amber_lake', 'Lower Tunguska': 'lower_tunguska_river', 'Old Burg': 'old_burg_lake',
                 'Seversky Donets': 'seversky_donets_river'}


def key(s):
    return re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()


def grams(s):
    """'8 kg' -> 8000, '14.5 kg' -> 14500, '270 g' -> 270, '—' / '' -> None."""
    m = re.fullmatch(r'\s*([\d.,]+)\s*(kg|g)\s*', s or '')
    if not m:
        return None
    v = float(m.group(1).replace(',', '.'))
    return int(round(v * 1000)) if m.group(2) == 'kg' else int(round(v))


def merge(fish, waters):
    """fish / waters: lists as in fish.json / waterbodies.json. Updates fish in place; returns a list of problems."""
    by_name = {key(f['names']['en']): f for f in fish}
    water_ids = {key(w['names']['en']): w['id'] for w in waters}
    water_ids.update({key(k): v for k, v in WATER_ALIASES.items()})
    problems, seen = [], set()
    with open(os.path.join(DATA, 'trophies.tsv'), encoding='utf-8') as fh:
        rows = list(csv.reader(fh, delimiter='\t'))[1:]
    for row in rows:
        if not row or not row[0].strip():
            continue
        name, trophy, super_trophy, found = (row + ['', '', '', ''])[:4]
        f = by_name.get(key(name))
        if not f:
            problems.append(f'unknown fish in trophies.tsv: {name!r}')
            continue
        ids = []
        for w in found.split(','):
            wid = water_ids.get(key(w))
            if wid:
                ids.append(wid)
            elif w.strip():
                problems.append(f'unknown waterbody {w.strip()!r} for {name!r}')
        f['trophy_g'], f['super_trophy_g'], f['waters'] = grams(trophy), grams(super_trophy), sorted(set(ids))
        if f['trophy_g'] is None:
            problems.append(f'no trophy weight for {name!r}')
        seen.add(f['id'])
    for f in fish:
        if f['id'] not in seen:
            for k in ('trophy_g', 'super_trophy_g', 'waters'):
                f.pop(k, None)
    return problems


def main():
    fpath, wpath = os.path.join(DATA, 'fish.json'), os.path.join(DATA, 'waterbodies.json')
    data = json.load(open(fpath, encoding='utf-8'))
    waters = json.load(open(wpath, encoding='utf-8'))['waterbodies']
    problems = merge(data['fish'], waters)
    for p in problems:
        print('!', p)
    missing = [f['names']['en'] for f in data['fish'] if 'trophy_g' not in f]
    if missing:
        print('fish without trophy data (no trophy levels, no waterbody check):', ', '.join(missing))
    with open(fpath, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    print(f'{len(data["fish"]) - len(missing)} of {len(data["fish"])} fish have trophy data')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
