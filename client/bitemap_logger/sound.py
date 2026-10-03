"""Short notification sounds, generated on first use (no audio files shipped).

winsound has no volume control, so the loudness is baked into the generated WAV: one file per sound and
volume step (5 % steps), created on demand in %APPDATA%\\BiteMap.
"""
import math
import os
import struct
import wave
import winsound

from .paths import USER_DIR

SOUNDS = {
    # name: list of (frequency Hz, duration s)
    'bite': [(880, 0.12), (0, 0.05), (1175, 0.12), (0, 0.05), (1568, 0.18)],
    'catch': [(659, 0.08), (988, 0.12)],
}
MAX_AMPLITUDE = 0.35   # 100 % = the original fixed alert loudness
_volume = 0.5


def set_volume(v):
    """0.0 .. 1.0"""
    global _volume
    try:
        _volume = min(1.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        pass


def get_volume():
    return _volume


def _path(name, step):
    # 'v2' in the name: files generated with the earlier, louder scale must not be reused
    return os.path.join(USER_DIR, f'sound_v2_{name}_{step:03d}.wav')


def _make(name, step, path):
    amp = MAX_AMPLITUDE * step / 100
    rate, frames = 44100, bytearray()
    for freq, dur in SOUNDS[name]:
        n = int(rate * dur)
        for i in range(n):
            env = min(1.0, i / 400, (n - i) / 800)  # click-free attack/release
            v = amp * env * math.sin(2 * math.pi * freq * i / rate) if freq else 0.0
            frames += struct.pack('<h', int(v * 32767))
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def play(name):
    step = int(round(_volume * 20)) * 5  # 0, 5, ..., 100
    if step <= 0 or name not in SOUNDS:
        return
    try:
        p = _path(name, step)
        if not os.path.exists(p):
            _make(name, step, p)
        winsound.PlaySound(p, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass
