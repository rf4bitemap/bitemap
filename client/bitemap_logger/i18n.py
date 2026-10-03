"""UI strings. locales/<lang>.json, English is the fallback for missing keys."""
import json
import locale
import os

from .paths import LOCALES

LANGS = {'en': 'English', 'de': 'Deutsch', 'ru': 'Русский'}
_strings = {}
_fallback = {}
current = 'en'


def _load(lang):
    try:
        with open(os.path.join(LOCALES, f'{lang}.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def system_lang():
    try:
        code = (locale.getlocale()[0] or '').lower()
    except Exception:
        code = ''
    for lang, names in (('de', ('de', 'german')), ('ru', ('ru', 'russian'))):
        if code.startswith(names):
            return lang
    return 'en'


def set_lang(lang):
    global _strings, _fallback, current
    if lang == 'auto' or lang not in LANGS:
        lang = system_lang()
    current = lang
    _fallback = _load('en')
    _strings = _load(lang) if lang != 'en' else _fallback


def t(key, **kw):
    s = _strings.get(key) or _fallback.get(key) or key
    return s.format(**kw) if kw else s
