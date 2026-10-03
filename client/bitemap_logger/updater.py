"""Self-update from the GitHub releases.

Every release carries a latest.json: a manifest (version, zip URL, size, SHA-256) signed in CI with an Ed25519 key.
The app only installs a zip that matches a manifest signed by PUBLIC_KEY.

Installing, in short:
  1. the running app downloads + checks the zip and unpacks it to <app folder>\\.bitemap-update\\<version>\\BiteMapLogger
  2. it starts the *new* BiteMapLogger.exe from there with --apply-update and closes itself
  3. that helper waits for the old app to exit, moves the old files aside (*.old), copies the new ones in, starts the
     updated app and exits; if anything fails it puts the old files back
Only our own top-level entries (BiteMapLogger.exe, _internal) are touched - never anything else in the folder.
"""
import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass

import requests

from . import PROJECT_URL, __version__
from .ed25519 import verify
from .paths import FROZEN, USER_DIR

log = logging.getLogger(__name__)

PUBLIC_KEY = base64.b64decode('eL4yrTVFX42goUaf9ZqLHyVMGgIjTsSKfgSt9NUOOGk=')
PRODUCT = 'BiteMapLogger'
EXE = 'BiteMapLogger.exe'
INTERNAL = '_internal'
# BITEMAP_UPDATE_URL: test a manifest from somewhere else (it still has to be signed with the release key)
MANIFEST_URL = os.environ.get('BITEMAP_UPDATE_URL') or PROJECT_URL + '/releases/latest/download/latest.json'
UPDATE_DIR = '.bitemap-update'
TEMP_STAGING = os.path.join(tempfile.gettempdir(), 'BiteMapUpdate')
STAGING = None   # fixed staging folder (tests); normally staging_dir() decides
UPDATE_LOG = os.path.join(USER_DIR, 'update.log')
CHECK_EVERY_S = 6 * 3600
DETACHED = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


class UpdateError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code   # 'blocked': files vanished right after unpacking (antivirus)


@dataclass
class Release:
    version: str
    file: str
    url: str
    size: int
    sha256: str

    @property
    def page(self):
        return f'{PROJECT_URL}/releases/tag/v{self.version}'


def parse_version(v):
    try:
        return tuple(int(p) for p in str(v).strip().lstrip('v').split('.'))
    except ValueError:
        return None


def auto_check_enabled():
    """Only the installed app checks on its own; running from source is for development."""
    return FROZEN or bool(os.environ.get('BITEMAP_UPDATE_URL'))


def fetch_latest(session=None, url=None):
    """The newest release if it is newer than this app, else None. Raises on network errors or a bad signature."""
    s = session or requests
    r = s.get(url or MANIFEST_URL, timeout=15, headers={'User-Agent': f'BiteMapLogger/{__version__}'})
    if r.status_code == 404:   # release without a manifest
        return None
    r.raise_for_status()
    d = r.json()
    manifest, sig = d['manifest'], base64.b64decode(d['signature'])
    if not verify(PUBLIC_KEY, manifest.encode('utf-8'), sig):
        raise UpdateError('update manifest has an invalid signature')
    m = json.loads(manifest)
    if m.get('product') != PRODUCT:
        raise UpdateError('update manifest is for a different product')
    rel = Release(str(m['version']), str(m['file']), str(m['url']), int(m['size']), str(m['sha256']).lower())
    new, cur = parse_version(rel.version), parse_version(__version__)
    local_test = rel.url.startswith(('http://127.0.0.1:', 'http://localhost:'))
    if not new or os.path.basename(rel.file) != rel.file or not (rel.url.startswith('https://') or local_test):
        raise UpdateError('update manifest is malformed')
    return rel if cur and new > cur else None


def install_dir():
    return os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else None


def self_update_blocker(target=None):
    """Why this install can't update itself (None = it can): 'source', 'layout' or 'readonly'."""
    target = target or install_dir()
    if not target:
        return 'source'
    if not (os.path.isfile(os.path.join(target, EXE)) and os.path.isdir(os.path.join(target, INTERNAL))):
        return 'layout'   # renamed exe or an unexpected layout: don't guess, let the user update by hand
    probe = os.path.join(target, f'.bitemap-write-test-{os.getpid()}')
    try:
        with open(probe, 'w') as f:
            f.write('x')
        os.remove(probe)
    except OSError:
        return 'readonly'   # e.g. unpacked to Program Files
    return None


def staging_dir():
    """Where updates are downloaded and unpacked: next to the installed app. Antivirus scanners are far more
    suspicious of a new exe in %TEMP%, and an exclusion the user set for the app folder covers this too."""
    if STAGING:
        return STAGING
    target = install_dir()
    if target and not self_update_blocker(target):
        return os.path.join(target, UPDATE_DIR)
    return TEMP_STAGING


def download(rel, progress=None, session=None):
    """Download the release zip to the staging folder and check size + SHA-256. Returns the zip path."""
    os.makedirs(staging_dir(), exist_ok=True)
    dest = os.path.join(staging_dir(), rel.file)
    part = dest + '.part'
    h, got, last = hashlib.sha256(), 0, -1
    s = session or requests
    with s.get(rel.url, stream=True, timeout=30, headers={'User-Agent': f'BiteMapLogger/{__version__}'}) as r:
        r.raise_for_status()
        with open(part, 'wb') as f:
            for chunk in r.iter_content(1 << 16):
                got += len(chunk)
                if got > rel.size:
                    raise UpdateError('download is larger than announced')
                h.update(chunk)
                f.write(chunk)
                pct = got * 100 // max(rel.size, 1)
                if progress and pct != last:
                    last = pct
                    progress(pct)
    if got != rel.size or h.hexdigest() != rel.sha256:
        _remove(part)
        raise UpdateError('download is damaged (checksum mismatch)')
    os.replace(part, dest)
    return dest


def extract(zip_path, rel):
    """Unpack to <staging>\\<version>; returns the folder holding the new BiteMapLogger.exe."""
    root = os.path.join(staging_dir(), rel.version)
    _remove(root)
    with zipfile.ZipFile(zip_path) as z:
        files = [n for n in z.namelist() if not n.endswith('/')]
        z.extractall(root)   # extractall drops absolute paths and '..'
    if f'BiteMapLogger/{EXE}' not in files or not any(n.startswith(f'BiteMapLogger/{INTERNAL}/') for n in files):
        raise UpdateError('update package has an unexpected layout')
    # unpacked fine but gone right away: an antivirus scanner quarantined it (unsigned exe, common false positive)
    missing = [n for n in files if not os.path.isfile(os.path.join(root, *n.split('/')))]
    if missing:
        log.error('%d of %d files vanished after unpacking to %s: %s', len(missing), len(files), root, missing[:20])
        first = f'BiteMapLogger/{EXE}' if f'BiteMapLogger/{EXE}' in missing else missing[0]
        raise UpdateError(f'{os.path.basename(first)} was removed right after unpacking (antivirus?)', code='blocked')
    return os.path.join(root, 'BiteMapLogger')


def _child_env():
    env = dict(os.environ)
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'   # we start a *different* frozen app
    for k in ('TCL_LIBRARY', 'TK_LIBRARY'):
        env.pop(k, None)
    return env


def launch_helper(app_dir, target):
    """Start the new version's exe as the installer. Returns (process, ready_file); the helper creates ready_file
    once it is running and waiting for us to exit."""
    if not os.path.isfile(os.path.join(app_dir, EXE)):
        raise UpdateError(f'{EXE} was removed right after unpacking (antivirus?)', code='blocked')
    ready = os.path.join(os.path.dirname(os.path.dirname(app_dir)), f'ready-{os.getpid()}')   # in <staging>
    _remove(ready)
    proc = subprocess.Popen([os.path.join(app_dir, EXE), '--apply-update', target, str(os.getpid()), ready],
                            cwd=app_dir, env=_child_env(), creationflags=DETACHED, close_fds=True)
    return proc, ready


def cleanup(target=None):
    """After an update: remove the staging folders and any *.old leftovers. Errors are ignored."""
    _remove(STAGING or TEMP_STAGING)
    target = target or install_dir()
    if target:
        _remove(os.path.join(target, UPDATE_DIR))
        for name in (EXE, INTERNAL):
            _remove(os.path.join(target, name + '.old'))


# ------------------------------------------------------------------------------------------------ helper process
def _remove(path):
    def unlock(func, p, _exc):
        try:
            os.chmod(p, 0o666)
            func(p)
        except OSError:
            pass
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, onexc=unlock)
        elif os.path.lexists(path):
            os.remove(path)
    except OSError as e:
        log.info('could not remove %s: %s', path, e)


def _retry(fn, *args, attempts=40, delay=0.5):
    """Antivirus scanners briefly lock fresh files; retry a few seconds before giving up."""
    for i in range(attempts):
        try:
            return fn(*args)
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


def _wait_for_exit(pid, timeout):
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    handle = k32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
    if not handle:
        return True   # already gone
    try:
        return k32.WaitForSingleObject(ctypes.c_void_p(handle), int(timeout * 1000)) == 0
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))


def swap(src, target):
    """Replace our entries in target with the ones from src; on failure restore the old ones. Returns True/False."""
    names = sorted(os.listdir(src))
    moved = []
    try:
        for name in names:
            dst = os.path.join(target, name)
            _remove(dst + '.old')
            if os.path.lexists(dst):
                _retry(os.rename, dst, dst + '.old')
            moved.append(name)
            if os.path.isdir(os.path.join(src, name)):
                _retry(shutil.copytree, os.path.join(src, name), dst)
            else:
                _retry(shutil.copy2, os.path.join(src, name), dst)
            log.info('installed %s', name)
    except Exception:
        log.exception('update failed, restoring the previous version')
        for name in reversed(moved):
            dst = os.path.join(target, name)
            try:
                _remove(dst)
                if os.path.lexists(dst + '.old'):
                    _retry(os.rename, dst + '.old', dst)
            except OSError:
                log.exception('could not restore %s', name)
        return False
    for name in names:
        _remove(os.path.join(target, name + '.old'))
    return True


def apply_update(target, pid, ready=None, src=None, relaunch=True):
    """Runs in the new version (started with --apply-update): wait for the old app to exit, swap, restart."""
    src = src or os.path.dirname(os.path.abspath(sys.executable))
    target = os.path.abspath(target)
    log.info('update %s -> %s (waiting for pid %s)', src, target, pid)
    if self_update_blocker(target) or os.path.normcase(src) == os.path.normcase(target):
        log.error('refusing to update %s', target)
        return False
    if ready:
        with open(ready, 'w') as f:
            f.write(str(os.getpid()))
    if not _wait_for_exit(pid, 90):
        log.error('the old app did not exit, update cancelled')
        return False
    ok = swap(src, target)
    log.info('update %s', 'done' if ok else 'rolled back')
    if relaunch:
        subprocess.Popen([os.path.join(target, EXE), '--updated' if ok else '--update-failed'], cwd=target,
                         env=_child_env(), creationflags=DETACHED, close_fds=True)
    return ok


def apply_update_main(args):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s',
                        handlers=[logging.FileHandler(UPDATE_LOG, encoding='utf-8')])
    try:
        target, pid, ready = args[0], int(args[1]), args[2] if len(args) > 2 else None
        return 0 if apply_update(target, pid, ready) else 1
    except Exception:
        log.exception('update helper crashed')
        return 1
