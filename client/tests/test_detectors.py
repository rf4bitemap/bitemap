"""Detector tests on the sample screenshots (several resolutions)."""
import os
import sys

import cv2
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
SAMPLES = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'tools', 'samples')

from bitemap_logger import ocr  # noqa: E402

pytestmark = pytest.mark.skipif(not ocr.setup(), reason='tesseract not available')
needs_samples = pytest.mark.skipif(not os.path.isdir(SAMPLES), reason='sample screenshots not present (kept private)')
SIZES = [(1280, 720), (1600, 900), (1920, 1080), (2560, 1440), (3840, 2160)]


def scaled(name, size):
    img = cv2.imread(os.path.join(SAMPLES, name))
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA if size[0] < img.shape[1] else cv2.INTER_CUBIC)


@needs_samples
@pytest.mark.parametrize('size', SIZES)
def test_catch_card(size):
    from bitemap_logger.detect.catchcard import CatchCardDetector
    from bitemap_logger.gamedata import GameData
    f = scaled('catch_de_2000.webp', size)
    det = CatchCardDetector()
    g = det.find(f)
    assert g is not None
    r = det.read(f, g, GameData(), ('de', 'en', 'ru'))
    assert (r.fish_id, r.weight_g, r.length_cm, r.lang) == ('lm_b_bass', 6722, 79, 'de')


@needs_samples
@pytest.mark.parametrize('size', SIZES)
def test_no_card_on_hud(size):
    from bitemap_logger.detect.catchcard import CatchCardDetector
    assert CatchCardDetector().find(scaled('hud_de_2000.webp', size)) is None


@needs_samples
@pytest.mark.parametrize('size', SIZES)
def test_coords(size):
    from bitemap_logger.detect import coords
    f = scaled('hud_de_2000.webp', size)
    x, y, w, h = coords.region(*size)
    assert coords.read_coords(f[y:y + h, x:x + w], size[1]) == (73, 48)


@needs_samples
def test_coords_dim_night_hud():
    from bitemap_logger.detect import coords
    assert coords.read_coords(cv2.imread(os.path.join(SAMPLES, 'hud_live_2560_dim_coords.png')), 1440) == (71, 37)


@pytest.mark.parametrize('text,grams', [('6,722kg', 6722), ('6.722 kg', 6722), ('722 g', 722), ('6722kg', 6722),
                                        ('12,5 kg', 12500), ('1 234,567 кг', 1234567), ('85 г', 85), ('', None)])
def test_parse_weight(text, grams):
    from bitemap_logger.detect.catchcard import parse_weight
    assert parse_weight(text) == grams


def test_fish_matching():
    from bitemap_logger.gamedata import GameData
    gd = GameData()
    assert gd.match_fish('Forellenbarsch')[0] == 'lm_b_bass'
    assert gd.match_fish('Eorellenharsch')[0] == 'lm_b_bass'
    assert gd.match_fish('Spiegelkarpfen')[0] == 'm_carp'
    assert gd.match_fish('Карп зеркальный')[0] == 'm_carp'
    assert gd.match_fish('Mirror Carp')[0] == 'm_carp'
    assert gd.match_fish('xyzzy')[0] is None


@needs_samples
@pytest.mark.parametrize('size', SIZES)
def test_bite_icon(size):
    from bitemap_logger.detect.bite import BiteDetector
    for name, expected in (('bite_de_2000.webp', True), ('hud_de_2000.webp', False), ('catch_de_2000.webp', False)):
        f = scaled(name, size)
        d = BiteDetector()
        assert d.enabled
        x, y, w, h = d.region(*size)
        assert d.update(f[y:y + h, x:x + w], size[1]) is expected, name


@needs_samples
def test_bite_fires_once_per_bite():
    from bitemap_logger.detect.bite import BiteDetector
    bite, hud = scaled('bite_de_2000.webp', (1920, 1080)), scaled('hud_de_2000.webp', (1920, 1080))
    d = BiteDetector()
    x, y, w, h = d.region(1920, 1080)
    crop = lambda f: f[y:y + h, x:x + w]  # noqa: E731
    seq = [(0, bite), (0.5, bite), (1.0, hud), (1.5, bite), (2.0, hud), (5.0, hud), (6.0, bite)]
    fired = [t for t, f in seq if d.update(crop(f), 1080, now=100 + t)]
    assert fired == [0, 6.0]  # a short flicker doesn't re-trigger; a new bite after the re-arm time does


@needs_samples
def test_no_false_cards_on_live_frames():
    """Frames from a live session (2560x1440 top band) that earlier versions mistook for catch cards."""
    import glob
    from bitemap_logger.detect.catchcard import CatchCardDetector
    frames = glob.glob(os.path.join(SAMPLES, 'false_cards', '*.png'))
    if not frames:
        pytest.skip('no false-card samples')
    for f in frames:
        assert CatchCardDetector().find(cv2.imread(f), 1440) is None, os.path.basename(f)


@needs_samples
@pytest.mark.parametrize('size', SIZES)
def test_small_card_without_badge(size):
    """'Döbel 68 g 16 cm': short weight, no trophy badge -> the icon row sits further right than on big cards."""
    from bitemap_logger.detect.catchcard import CatchCardDetector
    from bitemap_logger.gamedata import GameData
    f = scaled('catch_de_small_nobadge_2000.webp', size)
    det = CatchCardDetector()
    g = det.find(f)
    assert g is not None
    r = det.read(f, g, GameData(), ('de', 'en', 'ru'))
    assert (r.fish_id, r.weight_g, r.length_cm) == ('e.chub', 68, 16)


@needs_samples
@pytest.mark.parametrize('size', SIZES)
def test_coords_on_bought_minimap(size):
    """With a bought map the compass shows the map texture behind '41:57' and sits a bit higher;
    the keepnet counter '8/100' just above must not be taken for coordinates."""
    from bitemap_logger.detect import coords
    f = scaled('hud_minimap_de.webp', size)
    x, y, w, h = coords.region(*size)
    assert coords.read_coords(f[y:y + h, x:x + w], size[1]) == (41, 57)


@needs_samples
@pytest.mark.parametrize('size', SIZES)
def test_no_coords_when_covered(size):
    """At the bite a notification covers the coordinates: read nothing rather than something wrong."""
    from bitemap_logger.detect import coords
    f = scaled('bite_de_2000.webp', size)
    x, y, w, h = coords.region(*size)
    assert coords.read_coords(f[y:y + h, x:x + w], size[1]) is None


def test_trophy_levels_by_weight():
    from bitemap_logger.gamedata import GameData
    gd = GameData()
    # Largemouth bass: trophy 6 kg, super trophy 7.5 kg
    assert [gd.trophy_level('lm_b_bass', w) for w in (5999, 6000, 7499, 7500)] == [0, 1, 1, 2]
    assert gd.trophy_level('chimaera', 9000) == 1          # European chimaera has no super trophy
    assert [gd.trophy_level('bs_salmon', w) for w in (13999, 14000, 20000)] == [0, 1, 2]   # Black Sea trout
    assert gd.trophy_level(None, 5000) == 0


def test_fish_per_waterbody():
    from bitemap_logger.gamedata import GameData
    gd = GameData()
    assert gd.lives_in('lm_b_bass', 'elk_lake') and not gd.lives_in('lm_b_bass', 'belaya_river')
    assert gd.lives_in('bs_salmon', 'norwegian_sea')       # waterbodies unknown: allowed anywhere
    assert gd.lives_in('c_bleak', 'old_burg_lake')
    # a garbled name prefers the fish that lives here (anywhere these match Beluga-Stör / Neiva) ...
    name = lambda fid: gd.fish[fid]['names']
    assert name(gd.match_fish('Lauga-Stör', langs=('de',), water='ladoga_archipelago')[0])['de'] == 'Ladoga-Stör'
    assert name(gd.match_fish('Nemva', langs=('en',), water='lower_tunguska_river')[0])['en'] == 'Nelma'
    assert name(gd.match_fish('Nemva', langs=('en',))[0])['en'] == 'Neiva'
    # ... but a clear match from elsewhere still wins (then the wrong waterbody is selected)
    assert gd.match_fish('Forellenbarsch', water='belaya_river')[0] == 'lm_b_bass'
