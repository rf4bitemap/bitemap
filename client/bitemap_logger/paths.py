"""Where things live, both when running from source and from the PyInstaller build."""
import os
import sys

FROZEN = getattr(sys, 'frozen', False)
PKG_DIR = os.path.dirname(os.path.abspath(__file__))

if FROZEN:
    BUNDLE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    ASSETS = os.path.join(BUNDLE_DIR, 'assets')
    LOCALES = os.path.join(BUNDLE_DIR, 'locales')
    GAME_DATA = os.path.join(BUNDLE_DIR, 'data')
    TESSERACT_DIRS = [os.path.join(BUNDLE_DIR, 'tesseract')]
else:
    REPO = os.path.dirname(os.path.dirname(PKG_DIR))
    ASSETS = os.path.join(PKG_DIR, 'assets')
    LOCALES = os.path.join(PKG_DIR, 'locales')
    GAME_DATA = os.path.join(REPO, 'data')
    TESSERACT_DIRS = [os.path.join(REPO, 'client', 'vendor', 'tesseract')]

TESSERACT_DIRS += [r'C:\Program Files\Tesseract-OCR', r'C:\Program Files (x86)\Tesseract-OCR']

USER_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'BiteMap')
os.makedirs(USER_DIR, exist_ok=True)
SETTINGS_FILE = os.path.join(USER_DIR, 'settings.json')
DB_FILE = os.path.join(USER_DIR, 'catches.db')
LOG_FILE = os.path.join(USER_DIR, 'bitemap.log')
DEBUG_DIR = os.path.join(USER_DIR, 'debug')
