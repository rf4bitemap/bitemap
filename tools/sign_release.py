"""Sign a release for the in-app updater.

    python tools/sign_release.py dist/BiteMapLogger-0.1.4-win64.zip      -> dist/latest.json
    python tools/sign_release.py --keygen <file>                         -> new key pair

The private key (base64 of the raw 32-byte Ed25519 seed) comes from $BITEMAP_SIGNING_KEY - a GitHub Actions secret.
The matching public key is built into the app (client/bitemap_logger/updater.py, PUBLIC_KEY); the app only installs
updates whose latest.json carries a valid signature from it.
"""
import base64
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'client'))
from bitemap_logger import PROJECT_URL, __version__  # noqa: E402
from bitemap_logger import updater  # noqa: E402


def raw_public(key):
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def keygen(path):
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    with open(path, 'x', encoding='ascii') as f:
        f.write(base64.b64encode(seed).decode() + '\n')
    print('private key written to', path)
    print('public key (put into updater.PUBLIC_KEY):', base64.b64encode(raw_public(key)).decode())


def sign(zip_path, key_b64):
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(key_b64.strip()))
    if raw_public(key) != updater.PUBLIC_KEY:
        sys.exit('BITEMAP_SIGNING_KEY does not match the public key built into the app (updater.PUBLIC_KEY)')
    name = os.path.basename(zip_path)
    if name != f'BiteMapLogger-{__version__}-win64.zip':
        sys.exit(f'{name} does not match the app version {__version__}')
    tag = os.environ.get('GITHUB_REF_NAME')
    if tag and tag != f'v{__version__}':
        sys.exit(f'tag {tag} does not match the app version {__version__}')
    h = hashlib.sha256()
    with open(zip_path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    manifest = json.dumps({
        'product': updater.PRODUCT,
        'version': __version__,
        'file': name,
        'url': f'{PROJECT_URL}/releases/download/v{__version__}/{name}',
        'size': os.path.getsize(zip_path),
        'sha256': h.hexdigest(),
        'published': datetime.now(timezone.utc).isoformat(timespec='seconds'),
    }, separators=(',', ':'))
    sig = key.sign(manifest.encode('utf-8'))
    out = os.path.join(os.path.dirname(zip_path), 'latest.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'manifest': manifest, 'signature': base64.b64encode(sig).decode()}, f, indent=1)
    print('signed', name, '->', out)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--keygen':
        keygen(sys.argv[2])
    elif len(sys.argv) == 2:
        key = os.environ.get('BITEMAP_SIGNING_KEY')
        if not key:
            sys.exit('BITEMAP_SIGNING_KEY is not set')
        sign(sys.argv[1], key)
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
