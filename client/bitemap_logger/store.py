"""Local catch log (SQLite in %APPDATA%\\BiteMap\\catches.db). The app works fully offline from this."""
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from .paths import DB_FILE

SCHEMA = """
CREATE TABLE IF NOT EXISTS catches (
    uuid TEXT PRIMARY KEY,
    caught_at TEXT NOT NULL,          -- UTC ISO-8601
    fish_id TEXT,
    name_text TEXT,
    weight_g INTEGER,
    length_cm REAL,
    badge TEXT,
    waterbody TEXT,
    x INTEGER,
    y INTEGER,
    coord_age REAL,                   -- seconds between last coordinate reading and the catch
    bite_at TEXT,
    game_lang TEXT,
    uploaded INTEGER NOT NULL DEFAULT 0,  -- 0 pending, 1 done, -1 rejected, 2 deleted on the server too
    upload_note TEXT,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_catches_time ON catches(caught_at);
"""
FIELDS = ('uuid', 'caught_at', 'fish_id', 'name_text', 'weight_g', 'length_cm', 'badge', 'waterbody', 'x', 'y',
          'coord_age', 'bite_at', 'game_lang', 'uploaded', 'upload_note', 'deleted')


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Store:
    def __init__(self, path=DB_FILE):
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def add(self, **c):
        c.setdefault('uuid', str(uuid.uuid4()))
        c.setdefault('caught_at', utcnow())
        cols = [k for k in FIELDS if k in c]
        with self._lock, self.db:
            self.db.execute(f'INSERT INTO catches ({",".join(cols)}) VALUES ({",".join("?" * len(cols))})',
                            [c[k] for k in cols])
        return self.get(c['uuid'])

    def get(self, uid):
        with self._lock:
            r = self.db.execute('SELECT * FROM catches WHERE uuid=?', (uid,)).fetchone()
        return dict(r) if r else None

    def update(self, uid, **fields):
        fields = {k: v for k, v in fields.items() if k in FIELDS and k != 'uuid'}
        if not fields:
            return
        with self._lock, self.db:
            self.db.execute(f'UPDATE catches SET {",".join(k + "=?" for k in fields)} WHERE uuid=?',
                            [*fields.values(), uid])

    def recent(self, limit=200):
        with self._lock:
            rows = self.db.execute('SELECT * FROM catches WHERE deleted=0 ORDER BY caught_at DESC LIMIT ?',
                                   (limit,)).fetchall()
        return [dict(r) for r in rows]

    def since(self, iso):
        with self._lock:
            rows = self.db.execute('SELECT * FROM catches WHERE deleted=0 AND caught_at>=? ORDER BY caught_at',
                                   (iso,)).fetchall()
        return [dict(r) for r in rows]

    def pending_upload(self, limit=50):
        """Complete, not yet uploaded catches (edits re-queue a catch by setting uploaded=0)."""
        with self._lock:
            rows = self.db.execute(
                'SELECT * FROM catches WHERE uploaded=0 AND deleted=0 AND fish_id IS NOT NULL AND waterbody IS NOT NULL '
                "AND waterbody<>'' AND x IS NOT NULL ORDER BY caught_at LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def pending_deletes(self, limit=50):
        """Catches the user deleted locally after they had been shared."""
        with self._lock:
            rows = self.db.execute('SELECT uuid FROM catches WHERE deleted=1 AND uploaded=1 LIMIT ?', (limit,)).fetchall()
        return [r['uuid'] for r in rows]
