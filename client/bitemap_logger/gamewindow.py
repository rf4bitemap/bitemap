"""Find the Russian Fishing 4 window and grab pixels from it (screen capture only, no game memory access)."""
import ctypes
import ctypes.wintypes as wt
import threading

import mss
import numpy as np

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels on scaled displays
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

TITLES = ('Russian Fishing 4', 'Русская Рыбалка 4')
_EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def find_game_window():
    found = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            t = _title(hwnd)
            if any(t.startswith(x) for x in TITLES):
                found.append(hwnd)
        return True
    user32.EnumWindows(_EnumProc(cb), 0)
    return found[0] if found else None


def client_rect(hwnd):
    """Screen rectangle of the window's client area -> dict(left, top, width, height) or None."""
    if not hwnd or not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        return None
    r = wt.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = r.right - r.left, r.bottom - r.top
    if w < 320 or h < 240:
        return None
    return {'left': pt.x, 'top': pt.y, 'width': w, 'height': h}


def is_foreground(hwnd):
    return bool(hwnd) and user32.GetForegroundWindow() == hwnd


class Grabber:
    """mss is not thread-safe across threads, so keep one instance per thread."""

    def __init__(self):
        self._local = threading.local()

    def _sct(self):
        s = getattr(self._local, 'sct', None)
        if s is None:
            s = self._local.sct = mss.mss()
        return s

    def grab(self, rect, region=None):
        """BGR image of `region` (x, y, w, h relative to rect) or of the whole rect."""
        if region:
            x, y, w, h = (int(v) for v in region)
            box = {'left': rect['left'] + x, 'top': rect['top'] + y, 'width': max(1, w), 'height': max(1, h)}
        else:
            box = rect
        shot = self._sct().grab(box)
        return np.asarray(shot)[:, :, :3].copy()
