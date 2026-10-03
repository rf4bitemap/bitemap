"""Sends finished catches to the BiteMap server in the background (anonymous install token, no account)."""
import logging
import threading
import time

import requests

from . import __version__

log = logging.getLogger(__name__)
API = '/api/v1'


class Uploader(threading.Thread):
    def __init__(self, settings, store, on_change=None, interval=20):
        super().__init__(daemon=True, name='uploader')
        self.settings, self.store, self.on_change = settings, store, on_change
        self.interval = interval
        self._wake = threading.Event()
        self._halt = threading.Event()
        self.last_error = None
        self.outdated = False
        self.session = requests.Session()
        self.session.headers['User-Agent'] = f'BiteMapLogger/{__version__}'

    def kick(self):
        self._wake.set()

    def stop(self):
        self._halt.set()
        self._wake.set()

    def _url(self, path):
        return self.settings['server_url'].rstrip('/') + API + path

    def _ensure_token(self):
        if self.settings.get('install_token'):
            return True
        r = self.session.post(self._url('/installs'), json={'client_version': __version__}, timeout=15)
        r.raise_for_status()
        d = r.json()
        self.settings['install_id'] = d['install_id']
        self.settings.set('install_token', d['token'])
        return True

    def _payload(self, c):
        return {k: c[k] for k in ('uuid', 'caught_at', 'fish_id', 'weight_g', 'length_cm', 'badge', 'waterbody',
                                  'x', 'y', 'coord_age', 'bite_at', 'game_lang')}

    def upload_once(self):
        if not self.settings.get('upload') or not self.settings.get('server_url'):
            return 0
        if self.settings.get('install_token'):
            for uid in self.store.pending_deletes():
                r = self.session.delete(self._url(f'/catches/{uid}'), timeout=15,
                                        headers={'Authorization': 'Bearer ' + self.settings['install_token']})
                if r.ok:
                    self.store.update(uid, uploaded=2)
        batch = self.store.pending_upload()
        if not batch:
            return 0
        self._ensure_token()
        r = self.session.post(self._url('/catches'), json={'client_version': __version__,
                                                            'catches': [self._payload(c) for c in batch]},
                              headers={'Authorization': 'Bearer ' + self.settings['install_token']}, timeout=20)
        if r.status_code == 401:  # token unknown to the server (e.g. database reset) -> register again
            self.settings.set('install_token', '')
            raise RuntimeError('install token rejected, re-registering')
        self.outdated = r.status_code == 426   # server no longer accepts this version; catches wait for the update
        if self.outdated:
            raise RuntimeError('this version is too old for the server, update required')
        r.raise_for_status()
        d = r.json()
        for uid in d.get('accepted', []):
            self.store.update(uid, uploaded=1, upload_note=None)
        for rej in d.get('rejected', []):
            self.store.update(rej['uuid'], uploaded=-1, upload_note=rej.get('reason', 'rejected'))
        return len(batch)

    def run(self):
        while not self._halt.is_set():
            try:
                n = self.upload_once()
                self.last_error = None
                if n and self.on_change:
                    self.on_change()
            except Exception as e:
                self.last_error = str(e)
                log.info('upload failed: %s', e)
            self._wake.wait(600 if self.outdated else self.interval)
            self._wake.clear()
