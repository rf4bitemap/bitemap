"""Replay test: feed sample screenshots through the real engine (no game, no screen capture needed).

Timeline: fishing HUD (coordinates visible) -> catch card -> HUD again. Expect exactly one catch with
fish, weight, length, trophy badge and the spot that was read before the card appeared.
Needs Tesseract with eng+deu (set BITEMAP_TESSERACT or install to client/vendor/tesseract).
"""
import os
import sys
import time

import cv2
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
SAMPLES = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'tools', 'samples')


pytestmark = pytest.mark.skipif(not os.path.isdir(SAMPLES), reason='sample screenshots not present (kept private)')


@pytest.fixture()
def engine_env(tmp_path, monkeypatch):
    from bitemap_logger import engine as engine_mod, ocr, sound
    if not ocr.setup():
        pytest.skip('tesseract not available')
    monkeypatch.setattr(sound, 'play', lambda name: None)
    from bitemap_logger.gamedata import GameData
    from bitemap_logger.store import Store
    settings = {'waterbody': 'elk_lake', 'game_lang': 'auto', 'bite_alert': True, 'save_debug_images': False}
    store = Store(str(tmp_path / 'c.db'))
    events = []
    eng = engine_mod.Engine(settings, store, GameData(), lambda k, d: events.append((k, d)))
    return eng, store, events


@pytest.mark.parametrize('size', [(1920, 1080), (2560, 1440), (1600, 900)])
def test_replay(engine_env, size, monkeypatch):
    eng, store, events = engine_env
    W, H = size
    hud = cv2.resize(cv2.imread(os.path.join(SAMPLES, 'hud_de_2000.webp')), size, interpolation=cv2.INTER_AREA)
    card = cv2.resize(cv2.imread(os.path.join(SAMPLES, 'catch_de_2000.webp')), size, interpolation=cv2.INTER_AREA)
    clock = {'t': 1000.0}
    bite = cv2.resize(cv2.imread(os.path.join(SAMPLES, 'bite_de_2000.webp')), size, interpolation=cv2.INTER_AREA)
    timeline = [(0, hud), (3, bite), (5, card), (8, hud)]

    def frame():
        cur = timeline[0][1]
        for start, img in timeline:
            if clock['t'] - 1000.0 >= start:
                cur = img
        return cur

    class FakeGrab:
        def grab(self, rect, region=None):
            f = frame()
            if not region:
                return f.copy()
            x, y, w, h = region
            return f[y:y + h, x:x + w].copy()

    monkeypatch.setattr(eng, 'grab', FakeGrab())
    monkeypatch.setattr(eng, '_rect', lambda: {'left': 0, 'top': 0, 'width': W, 'height': H})
    from bitemap_logger import engine as engine_mod
    monkeypatch.setattr(engine_mod.time, 'sleep', lambda s: clock.__setitem__('t', clock['t'] + s))
    monkeypatch.setattr(engine_mod.time, 'time', lambda: clock['t'])
    while clock['t'] - 1000.0 < 10:
        eng._tick(clock['t'])
        clock['t'] += engine_mod.TICK

    catches = [d for k, d in events if k == 'catch']
    assert len(catches) == 1, events
    c = catches[0]
    assert c['fish_id'] == 'lm_b_bass'
    assert c['weight_g'] == 6722
    assert c['length_cm'] == 79
    assert c['badge'] == 'trophy'
    assert (c['x'], c['y']) == (73, 48)
    assert c['waterbody'] == 'elk_lake'
    assert c['game_lang'] == 'de'
    assert c['bite_at'] is not None
    assert [k for k, _ in events].count('bite') == 1
    assert store.pending_upload()[0]['uuid'] == c['uuid']


def test_card_read_after_fade_in(engine_env, monkeypatch):
    """The card is read once it has stopped fading in, not on the first (half transparent) frame."""
    eng, store, events = engine_env
    W, H = 1920, 1080
    hud = cv2.resize(cv2.imread(os.path.join(SAMPLES, 'hud_de_2000.webp')), (W, H), interpolation=cv2.INTER_AREA)
    card = cv2.resize(cv2.imread(os.path.join(SAMPLES, 'catch_de_2000.webp')), (W, H), interpolation=cv2.INTER_AREA)
    clock = {'t': 0.0}
    alphas = []

    class FadeGrab:
        def grab(self, rect, region=None):
            a = min(1.0, 0.3 + clock['t'] / 0.4)   # detected at 30 % opacity, fully visible after 0.28 s
            alphas.append(a)
            x, y, w, h = region
            return cv2.addWeighted(card, a, hud, 1 - a, 0)[y:y + h, x:x + w].copy()

    from bitemap_logger import engine as engine_mod
    monkeypatch.setattr(eng, 'grab', FadeGrab())
    monkeypatch.setattr(engine_mod.time, 'sleep', lambda s: clock.__setitem__('t', clock['t'] + s))
    monkeypatch.setattr(engine_mod.time, 'time', lambda: clock['t'])
    rect = {'left': 0, 'top': 0, 'width': W, 'height': H}
    geom = eng.card.find(card[:int(H * 0.32)], H)
    eng._settled_band(rect, geom)
    assert alphas[-1] == 1.0, alphas   # the band that gets read is the fully visible one
    eng._handle_card(rect, geom)
    c = [d for k, d in events if k == 'catch'][0]
    assert (c['fish_id'], c['weight_g'], c['length_cm']) == ('lm_b_bass', 6722, 79)
