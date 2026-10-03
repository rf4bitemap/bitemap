"""Fish / waterbody reference data shared with the desktop app (repo folder data/)."""
import json
import os

DATA_DIR = os.environ.get('BITEMAP_DATA', os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'data'))


def _load(name, key):
    with open(os.path.join(DATA_DIR, name), encoding='utf-8') as f:
        return {x['id']: x for x in json.load(f)[key]}


FISH = _load('fish.json', 'fish')
WATERBODIES = _load('waterbodies.json', 'waterbodies')


def coord_range(water_id):
    lo, hi = (WATERBODIES.get(water_id) or {}).get('coord_range') or (0, 999)
    return lo - 5, hi + 5


def max_weight_g(fish_id):
    rec = (FISH.get(fish_id) or {}).get('record_g')
    return max(rec * 1.25, rec + 500) if rec else 2_000_000
