"""The capture loop: catch card -> catch, HUD -> coordinates, lower-left icon -> bite alert."""
import logging
import os
import threading
import time
from datetime import datetime, timezone

import cv2

from . import gamewindow, ocr, sound
from .detect.bite import BiteDetector
from .detect.catchcard import CatchCardDetector
from .detect.coords import CoordTracker, read_coords, region as coord_region
from .paths import DEBUG_DIR
from .store import utcnow

log = logging.getLogger(__name__)

TICK = 0.25            # seconds between card checks
COORD_EVERY = 1.5      # seconds between coordinate reads
CARD_GONE_AFTER = 1.0  # card must be absent this long before the next one counts
COORD_MAX_AGE = 900    # don't attach coordinates older than this to a catch
BITE_MAX_AGE = 900


class Engine(threading.Thread):
    def __init__(self, settings, store, gamedata, emit):
        super().__init__(daemon=True, name='engine')
        self.settings, self.store, self.gd, self.emit = settings, store, gamedata, emit
        self.card = CatchCardDetector()
        self.bite = BiteDetector()
        self.coords = CoordTracker()
        self.grab = gamewindow.Grabber()
        self.paused = False
        self._halt = threading.Event()
        self._hwnd, self._hwnd_at = None, 0.0
        self._card_logged = False
        self._card_last_seen = 0.0
        self._coord_at = 0.0
        self._coord_thread = None     # coordinates are read on their own thread (OCR takes ~0.3 s)
        self._coord_lock = threading.Lock()
        self._last_rect = None
        self._last_bite = None
        self._bite_spot = None
        self._lang_pref = []
        self._status = None

    def stop(self):
        self._halt.set()

    # ------------------------------------------------------------------ helpers
    def _set_status(self, s, **extra):
        if s != self._status:
            self._status = s
            self.emit('status', {'state': s, **extra})

    def _rect(self):
        now = time.time()
        if not self._hwnd or now - self._hwnd_at > 3:
            self._hwnd, self._hwnd_at = gamewindow.find_game_window(), now
        return gamewindow.client_rect(self._hwnd)

    def langs(self):
        gl = self.settings.get('game_lang', 'auto')
        if gl in ('en', 'de', 'ru'):
            return (gl,)
        order = self._lang_pref + [l for l in ('de', 'en', 'ru') if l not in self._lang_pref]
        return tuple(order)

    # ------------------------------------------------------------------ main loop
    def run(self):
        if not ocr.setup():
            self._set_status('no_ocr')
            return
        self._coord_thread = threading.Thread(target=self._coord_loop, daemon=True, name='coords')
        self._coord_thread.start()
        while not self._halt.is_set():
            t0 = time.time()
            try:
                self._tick(t0)
            except Exception as e:
                log.exception('engine tick failed')
                self.emit('error', str(e))
                time.sleep(1)
            time.sleep(max(0.02, TICK - (time.time() - t0)))

    def _tick(self, now):
        if self.paused:
            self._set_status('paused')
            return
        rect = self._rect()
        if not rect:
            self._set_status('no_game')
            time.sleep(1)
            return
        W, H = rect['width'], rect['height']
        self._last_rect = rect
        self._set_status('watching', size=f'{W}x{H}')

        band = self.grab.grab(rect, (0, 0, W, int(H * 0.32)))
        geom = self.card.find(band, H)
        if geom:
            self._card_last_seen = now
            if not self._card_logged:
                self._handle_card(rect, geom)
            return
        if self._card_logged and now - self._card_last_seen > CARD_GONE_AFTER:
            self._card_logged = False

        if self._coord_thread is None and now - self._coord_at >= COORD_EVERY:  # no worker (tests): inline
            self._coord_at = now
            self._coord_step(rect, now)

        if self.bite.enabled:
            bx, by, bw, bh = self.bite.region(W, H)
            if self.bite.update(self.grab.grab(rect, (bx, by, bw, bh)), H, now):
                self._on_bite(rect, now)

    def _coord_loop(self):
        while not self._halt.is_set():
            t0 = time.time()
            try:
                rect = self._last_rect
                card_open = t0 - self._card_last_seen < CARD_GONE_AFTER  # the card hides the HUD
                if rect and not self.paused and not card_open:
                    self._coord_step(rect, t0)
            except Exception:
                log.exception('coordinate read failed')
            self._halt.wait(max(0.1, COORD_EVERY - (time.time() - t0)))

    def _coord_step(self, rect, now):
        x, y, w, h = coord_region(rect['width'], rect['height'])
        xy = read_coords(self.grab.grab(rect, (x, y, w, h)), rect['height'])
        with self._coord_lock:
            before = self.coords.value
            self.coords.feed(xy, now)
            changed = self.coords.value != before
        if changed:
            self.emit('coords', self.coords.value)

    def _on_bite(self, rect, now):
        """A fish bit: alert, and pin the spot *now* - the catch card comes later and hides the HUD."""
        self._last_bite = now
        self.emit('bite', now)
        if self.settings.get('bite_alert', True):
            sound.play('bite')
        x, y, w, h = coord_region(rect['width'], rect['height'])
        xy = read_coords(self.grab.grab(rect, (x, y, w, h)), rect['height'])
        with self._coord_lock:
            if xy and (self.coords.value is None or xy == self.coords.value):
                self.coords.feed(xy, now)
                self.coords.feed(xy, now)  # a clean read at bite time confirms on its own
        # notifications ("bail closed" etc.) often cover the coordinates right now: fall back to the last value
        self._bite_spot = self.coords.get(COORD_MAX_AGE, now)

    def _handle_card(self, rect, geom):
        W, H = rect['width'], rect['height']
        time.sleep(0.35)  # let the card's fade-in finish
        readings = []
        for attempt in range(3):
            band = self.grab.grab(rect, (0, 0, W, int(H * 0.32)))
            g = self.card.find(band, H) or geom
            r = self.card.read(band, g, self.gd, self.langs())
            readings.append((r, band))
            if r.fish_id and r.weight_g and len(readings) >= 2:
                prev = readings[-2][0]
                if prev.fish_id == r.fish_id and prev.weight_g == r.weight_g:
                    break
            time.sleep(0.3)
        # best reading: identified fish with a weight, else whatever has the highest name score
        r, band = max(readings, key=lambda rb: (bool(rb[0].fish_id), bool(rb[0].weight_g), rb[0].fish_score))
        self._card_logged = True
        if not r.fish_id and not r.weight_g:
            # nothing readable: almost certainly not a catch card (foliage, menus, ...) - don't log anything
            log.info('ignored card-like match: name %r, weight %r', r.name_text, r.weight_text)
            if self.settings.get('save_debug_images'):
                self._save_debug(band, 'ignored-' + time.strftime('%Y%m%d-%H%M%S'))
            return
        if r.lang and r.lang not in self._lang_pref[:1]:
            self._lang_pref = [r.lang] + [l for l in self._lang_pref if l != r.lang]

        xy, age = self.coords.get(COORD_MAX_AGE)
        bite_at = None
        if self._last_bite and time.time() - self._last_bite < BITE_MAX_AGE:
            bite_at = datetime.fromtimestamp(self._last_bite, timezone.utc).isoformat(timespec='seconds')
            if self._bite_spot and self._bite_spot[0]:  # the spot where the fish bit
                xy, age = self._bite_spot[0], self._bite_spot[1] + (time.time() - self._last_bite)
        self._last_bite = None
        self._bite_spot = None
        if r.fish_id and r.weight_g and not self.gd.plausible_weight(r.fish_id, r.weight_g):
            log.info('implausible weight %s g for %s (text %r)', r.weight_g, r.fish_id, r.weight_text)
        catch = self.store.add(
            caught_at=utcnow(), fish_id=r.fish_id, name_text=r.name_text, weight_g=r.weight_g,
            length_cm=r.length_cm, badge=r.badge, waterbody=self.settings.get('waterbody') or None,
            x=xy[0] if xy else None, y=xy[1] if xy else None, coord_age=round(age, 1) if xy else None,
            bite_at=bite_at, game_lang=r.lang)
        log.info('catch: %s %r %s g %s cm at %s (%s)', r.fish_id, r.name_text, r.weight_g, r.length_cm, xy, r.badge)
        if not r.fish_id or not r.weight_g or self.settings.get('save_debug_images'):
            self._save_debug(band, catch['uuid'])
        sound.play('catch')
        self.emit('catch', catch)

    def _save_debug(self, img, uid):
        try:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            cv2.imwrite(os.path.join(DEBUG_DIR, f'{uid}.png'), img)
        except Exception:
            pass
