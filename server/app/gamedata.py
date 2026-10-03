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


def trophy_level(fish_id, weight_g):
    """0 = normal, 1 = trophy, 2 = super trophy - from the weights in data/trophies.tsv (not the card's label)."""
    f = FISH.get(fish_id) or {}
    t, st = f.get('trophy_g'), f.get('super_trophy_g')
    if not weight_g or not t:
        return 0
    if st and weight_g >= st:
        return 2
    return 1 if weight_g >= t else 0


def lives_in(fish_id, water_id):
    """Whether the fish can be caught in that waterbody (fish without data: anywhere)."""
    waters = (FISH.get(fish_id) or {}).get('waters')
    return not waters or water_id in waters
