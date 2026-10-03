"""User settings, stored as JSON in %APPDATA%\\BiteMap\\settings.json."""
import json
import os
import threading

from . import DEFAULT_SERVER
from .paths import SETTINGS_FILE

DEFAULTS = {
    'ui_lang': 'auto',            # auto | en | de | ru
    'game_lang': 'auto',          # language of the RF4 client: auto | en | de | ru
    'waterbody': '',              # id from data/waterbodies.json
    'upload': True,               # share catches with the community map
    'server_url': DEFAULT_SERVER,
    'bite_alert': True,           # play a sound when a bite is detected
    'alert_volume': 0.5,          # 0..1, bite + catch sounds
    'save_debug_images': False,   # keep screenshots of unreadable catch cards in %APPDATA%\\BiteMap\\debug
    'install_id': '',
    'install_token': '',
    'window': None,
    'dev_tokens': {},             # install tokens for BITEMAP_SERVER overrides (local test servers)
}

OLD_DEFAULT_SERVERS = ('https://bitemap.example.org',)
_lock = threading.Lock()


class Settings(dict):
    def __init__(self):
        super().__init__(DEFAULTS)
        try:
            with open(SETTINGS_FILE, encoding='utf-8') as f:
                self.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
        if self.get('server_url') in OLD_DEFAULT_SERVERS:  # placeholder from pre-release builds
            self['server_url'] = DEFAULT_SERVER
        # development: BITEMAP_SERVER=http://127.0.0.1:8000 points the app at a local server without touching
        # the saved settings; the install token is kept per server so local tests don't clobber the real one
        self._server_override = os.environ.get('BITEMAP_SERVER')
        if self._server_override:
            self._real_token = self.get('install_token', '')
            self['server_url'] = self._server_override
            self['install_token'] = self.get('dev_tokens', {}).get(self._server_override, '')

    def _persisted(self):
        d = dict(self)
        if self._server_override:
            tokens = dict(d.get('dev_tokens') or {})
            tokens[self._server_override] = d.get('install_token', '')
            d['dev_tokens'] = tokens
            try:
                with open(SETTINGS_FILE, encoding='utf-8') as f:
                    saved = json.load(f)
            except (OSError, ValueError):
                saved = {}
            d['server_url'] = saved.get('server_url', DEFAULT_SERVER)
            d['install_token'] = self._real_token
        return d

    def save(self):
        with _lock:
            tmp = SETTINGS_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._persisted(), f, indent=1)
            os.replace(tmp, SETTINGS_FILE)

    def set(self, key, value):
        self[key] = value
        self.save()
