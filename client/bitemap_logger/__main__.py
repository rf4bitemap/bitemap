import logging
import logging.handlers
import sys


def selftest():
    """Headless check of the bundled pieces (used by CI and for bug reports). Writes selftest.txt, exit code 0/1."""
    import os
    import cv2
    import numpy as np
    from . import __version__, ocr
    from .detect.catchcard import CatchCardDetector
    from .gamedata import GameData
    from .paths import USER_DIR
    lines, ok = [f'BiteMap Logger {__version__}'], True

    def check(name, cond, detail=''):
        nonlocal ok
        ok &= bool(cond)
        lines.append(f"{'OK  ' if cond else 'FAIL'} {name} {detail}")
    check('tesseract', ocr.setup())
    langs = ocr.available_langs()
    check('languages', all(l in langs for l in ('eng', 'deu', 'rus')), ','.join(langs))
    det = CatchCardDetector()
    check('templates', det.weight_tpl is not None and det.ruler_tpl is not None)
    gd = GameData()
    check('game data', len(gd.fish) > 200 and len(gd.waterbodies) > 10, f'{len(gd.fish)} fish, {len(gd.waterbodies)} waters')
    img = np.full((60, 220), 40, np.uint8)
    cv2.putText(img, '73:48', (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 255, 3)
    t = ocr.read_line(ocr.prepare(img), ('en',), whitelist='0123456789:')
    check('ocr', t.replace(' ', '') == '73:48', repr(t))
    with open(os.path.join(USER_DIR, 'selftest.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    sys.exit(0 if ok else 1)


def main():
    if '--selftest' in sys.argv:
        selftest()
    from .paths import LOG_FILE
    logging.basicConfig(
        level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        handlers=[logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding='utf-8')]
        + ([logging.StreamHandler()] if sys.stderr else []))
    from . import i18n
    from .engine import Engine
    from .gamedata import GameData
    from .settings import Settings
    from .store import Store
    from .ui import App
    from .uploader import Uploader

    settings = Settings()
    i18n.set_lang(settings.get('ui_lang', 'auto'))
    from . import sound
    sound.set_volume(settings.get('alert_volume', 0.5))
    store = Store()
    gd = GameData()
    uploader = Uploader(settings, store)
    app = App(settings, store, gd, lambda emit: Engine(settings, store, gd, emit), uploader)
    uploader.on_change = lambda: app.events.put(('uploaded', None))  # Tk must only be touched by its own thread
    uploader.start()
    app.after(30000, app._tick_stats)
    app.mainloop()


if __name__ == '__main__':
    main()
