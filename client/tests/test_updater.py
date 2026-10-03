"""Updater: signature check, manifest handling, download checks and the file swap (no network)."""
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from bitemap_logger import __version__, updater  # noqa: E402
from bitemap_logger.ed25519 import verify  # noqa: E402

ed = pytest.importorskip('cryptography.hazmat.primitives.asymmetric.ed25519')
from cryptography.hazmat.primitives import serialization  # noqa: E402


def test_rfc8032_vectors():
    pk = bytes.fromhex('d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a')
    sig = bytes.fromhex('e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46b'
                        'd25bf5f0595bbe24655141438e7a100b')
    assert verify(pk, b'', sig)
    pk = bytes.fromhex('3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c')
    sig = bytes.fromhex('92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c'
                        '387b2eaeb4302aeeb00d291612bb0c00')
    assert verify(pk, b'\x72', sig)
    assert not verify(pk, b'\x73', sig)
    assert not verify(pk, b'\x72', sig[:32] + bytes(32))
    assert not verify(pk, b'\x72', sig[:-1])


def test_matches_reference_implementation():
    for i in range(20):
        key = ed.Ed25519PrivateKey.generate()
        pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        msg = os.urandom(i * 7)
        sig = key.sign(msg)
        assert verify(pub, msg, sig)
        assert not verify(pub, msg + b'!', sig)
        assert not verify(pub, msg, bytes([sig[0] ^ 1]) + sig[1:])


# ------------------------------------------------------------------------------------------------ manifest
@pytest.fixture()
def key(monkeypatch):
    k = ed.Ed25519PrivateKey.generate()
    monkeypatch.setattr(updater, 'PUBLIC_KEY',
                        k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    return k


def signed(key, **kw):
    m = {'product': 'BiteMapLogger', 'version': '99.0.0', 'file': 'BiteMapLogger-99.0.0-win64.zip',
         'url': 'https://example.invalid/BiteMapLogger-99.0.0-win64.zip', 'size': 3, 'sha256': 'ab' * 32}
    m.update(kw)
    text = json.dumps(m)
    return {'manifest': text, 'signature': base64.b64encode(key.sign(text.encode())).decode()}


class FakeResponse:
    def __init__(self, status=200, body=None, data=b''):
        self.status_code, self._body, self._data = status, body, data

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, n):
        for i in range(0, len(self._data), n):
            yield self._data[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSession:
    def __init__(self, resp):
        self.resp = resp

    def get(self, url, **kw):
        return self.resp


def test_fetch_latest(key):
    rel = updater.fetch_latest(FakeSession(FakeResponse(body=signed(key))))
    assert rel.version == '99.0.0' and rel.page.endswith('/releases/tag/v99.0.0')
    assert updater.fetch_latest(FakeSession(FakeResponse(body=signed(key, version=__version__)))) is None
    assert updater.fetch_latest(FakeSession(FakeResponse(body=signed(key, version='0.0.1')))) is None
    assert updater.fetch_latest(FakeSession(FakeResponse(status=404))) is None


@pytest.mark.parametrize('change', ['signature', 'manifest', 'product', 'file', 'url'])
def test_fetch_latest_rejects(key, change):
    body = signed(key)
    if change == 'signature':
        body = signed(ed.Ed25519PrivateKey.generate())        # signed by somebody else
    elif change == 'manifest':
        body['manifest'] = body['manifest'].replace('99.0.0', '98.0.0')
    elif change == 'product':
        body = signed(key, product='Other')
    elif change == 'file':
        body = signed(key, file='..\\evil.zip')
    elif change == 'url':
        body = signed(key, url='http://example.invalid/x.zip')
    with pytest.raises(updater.UpdateError):
        updater.fetch_latest(FakeSession(FakeResponse(body=body)))


def test_version_order():
    assert updater.parse_version('0.1.10') > updater.parse_version('0.1.9')
    assert updater.parse_version('v1.0') == (1, 0)
    assert updater.parse_version('test') is None


# ------------------------------------------------------------------------------------------------ download + unpack
def release(data, **kw):
    r = dict(version='99.0.0', file='BiteMapLogger-99.0.0-win64.zip', url='https://example.invalid/x.zip',
             size=len(data), sha256=hashlib.sha256(data).hexdigest())
    r.update(kw)
    return updater.Release(**r)


def test_download_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, 'STAGING', str(tmp_path))
    data = os.urandom(200_000)
    seen = []
    path = updater.download(release(data), progress=seen.append, session=FakeSession(FakeResponse(data=data)))
    assert open(path, 'rb').read() == data and seen[-1] == 100
    with pytest.raises(updater.UpdateError):   # tampered
        updater.download(release(data), session=FakeSession(FakeResponse(data=data[:-1] + b'x')))
    with pytest.raises(updater.UpdateError):   # bigger than announced
        updater.download(release(data, size=10), session=FakeSession(FakeResponse(data=data)))


def make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, content in entries.items():
            z.writestr(name, content)
    return buf.getvalue()


def test_extract(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, 'STAGING', str(tmp_path))
    zp = tmp_path / 'u.zip'
    zp.write_bytes(make_zip({'BiteMapLogger/BiteMapLogger.exe': 'new', 'BiteMapLogger/_internal/a.dll': 'a'}))
    app = updater.extract(str(zp), release(b''))
    assert open(os.path.join(app, 'BiteMapLogger.exe')).read() == 'new'
    zp.write_bytes(make_zip({'something/else.exe': 'x'}))
    with pytest.raises(updater.UpdateError):
        updater.extract(str(zp), release(b''))


# ------------------------------------------------------------------------------------------------ swap
def make_install(root, tag, extra=True):
    os.makedirs(os.path.join(root, '_internal', 'sub'))
    for rel, content in (('BiteMapLogger.exe', tag), ('_internal/base.dll', tag), ('_internal/sub/x.pyd', tag)):
        with open(os.path.join(root, rel), 'w') as f:
            f.write(content)
    if extra and tag == 'old':
        with open(os.path.join(root, '_internal', 'removed_in_new.dll'), 'w') as f:
            f.write(tag)


def read_tree(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root).replace('\\', '/')] = open(p).read()
    return out


def test_swap_replaces_only_our_files(tmp_path):
    target, src = str(tmp_path / 'install'), str(tmp_path / 'new')
    make_install(target, 'old')
    make_install(src, 'new')
    (tmp_path / 'install' / 'my notes.txt').write_text('keep me')
    assert updater.swap(src, target)
    assert read_tree(target) == {'BiteMapLogger.exe': 'new', '_internal/base.dll': 'new', '_internal/sub/x.pyd': 'new',
                                 'my notes.txt': 'keep me'}


def test_swap_rolls_back(tmp_path, monkeypatch):
    target, src = str(tmp_path / 'install'), str(tmp_path / 'new')
    make_install(target, 'old')
    make_install(src, 'new')
    before = read_tree(target)
    real = updater.shutil.copytree

    def broken(a, b, **kw):
        real(a, b, **kw)
        raise OSError('disk full')
    monkeypatch.setattr(updater.shutil, 'copytree', broken)
    assert not updater.swap(src, target)
    assert read_tree(target) == before


def test_apply_update(tmp_path):
    target, src = str(tmp_path / 'install'), str(tmp_path / 'new')
    make_install(target, 'old')
    make_install(src, 'new')
    gone = subprocess.Popen([sys.executable, '-c', 'pass'])
    gone.wait()
    ready = str(tmp_path / 'ready')
    assert updater.apply_update(target, gone.pid, ready=ready, src=src, relaunch=False)
    assert os.path.exists(ready)
    assert read_tree(target)['BiteMapLogger.exe'] == 'new'
    assert not any(n.endswith('.old') for n in os.listdir(target))


def test_apply_update_refuses_odd_targets(tmp_path):
    src = str(tmp_path / 'new')
    make_install(src, 'new')
    (tmp_path / 'desktop').mkdir()
    (tmp_path / 'desktop' / 'file.txt').write_text('x')
    assert not updater.apply_update(str(tmp_path / 'desktop'), 0, src=src, relaunch=False)
    assert os.listdir(tmp_path / 'desktop') == ['file.txt']
    assert updater.self_update_blocker(str(tmp_path / 'desktop')) == 'layout'
    assert updater.self_update_blocker(src) is None
