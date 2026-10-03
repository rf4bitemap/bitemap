"""BiteMap server: receives catches from BiteMap Logger and serves the community map."""
import hashlib
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Integer, and_, cast, func, select
from sqlalchemy.orm import Session

from . import gamedata
from .db import Catch, Install, SessionLocal, init_db

VERSION = '0.1.0'
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web')
MAX_BATCH = 100
MAX_CATCHES_PER_HOUR = 400          # per install; RF4 rarely exceeds ~200 fish/h even on fast spots
MAX_INSTALLS_PER_IP_HOUR = 10
MAX_AGE_DAYS = 30
TRUST_PROXY = os.environ.get('TRUST_PROXY', '1') == '1'

@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title='BiteMap', version=VERSION, docs_url='/api/docs', redoc_url=None, openapi_url='/api/openapi.json',
              lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)


def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------- rate limiting (in memory)
class SlidingWindow:
    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key, limit, window_s, cost=1):
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) + cost > limit:
                return False
            q.extend([now] * cost)
            return True


limiter = SlidingWindow()


def client_ip(request: Request):
    if TRUST_PROXY:
        # behind Cloudflare every request comes from a Cloudflare address; the visitor is in CF-Connecting-IP
        cf = request.headers.get('cf-connecting-ip')
        if cf:
            return cf.strip()
        fwd = request.headers.get('x-forwarded-for')
        if fwd:
            return fwd.split(',')[0].strip()
    return request.client.host if request.client else '?'


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def current_install(authorization: str = Header(default=''), s: Session = Depends(db)) -> Install:
    if not authorization.startswith('Bearer '):
        raise HTTPException(401, 'missing token')
    inst = s.scalar(select(Install).where(Install.token_hash == token_hash(authorization[7:].strip())))
    if not inst:
        raise HTTPException(401, 'unknown token')
    if inst.banned:
        raise HTTPException(403, 'install banned')
    return inst


# ---------------------------------------------------------------------------- write API
class InstallIn(BaseModel):
    client_version: str = Field('', max_length=32)


@app.post('/api/v1/installs')
def register(body: InstallIn, request: Request, s: Session = Depends(db)):
    if not limiter.allow('reg:' + client_ip(request), MAX_INSTALLS_PER_IP_HOUR, 3600):
        raise HTTPException(429, 'too many registrations')
    token = secrets.token_urlsafe(32)
    now = utcnow()
    inst = Install(id=secrets.token_hex(8), token_hash=token_hash(token), created_at=now, last_seen=now,
                   client_version=body.client_version)
    s.add(inst)
    s.commit()
    return {'install_id': inst.id, 'token': token}


class CatchIn(BaseModel):
    uuid: str = Field(..., max_length=64)   # format checked per item so one bad catch can't sink a batch
    caught_at: str
    fish_id: str = Field(..., max_length=48)
    waterbody: str = Field(..., max_length=48)
    x: int
    y: int
    weight_g: int
    length_cm: float | None = None
    badge: str | None = Field(None, max_length=16)
    coord_age: float | None = None
    bite_at: str | None = None
    game_lang: str | None = Field(None, max_length=4)


class CatchBatch(BaseModel):
    client_version: str = Field('', max_length=32)
    catches: list[CatchIn] = Field(..., max_length=MAX_BATCH)


UUID_RX = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def parse_time(s):
    try:
        t = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None
    if t.tzinfo:
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    return t


def validate(c: CatchIn, now):
    """Returns (reason, parsed_time) - reason None means the catch is plausible."""
    if not UUID_RX.match(c.uuid):
        return 'bad_uuid', None
    if c.fish_id not in gamedata.FISH:
        return 'unknown_fish', None
    if c.waterbody not in gamedata.WATERBODIES:
        return 'unknown_waterbody', None
    lo, hi = gamedata.coord_range(c.waterbody)
    if not (lo <= c.x <= hi and lo <= c.y <= hi):
        return 'coords_out_of_range', None
    if not (0 < c.weight_g <= gamedata.max_weight_g(c.fish_id)):
        return 'implausible_weight', None
    if c.length_cm is not None and not (0 < c.length_cm < 1000):
        return 'implausible_length', None
    if c.coord_age is not None and c.coord_age > 1800:
        return 'stale_coords', None
    t = parse_time(c.caught_at)
    if t is None:
        return 'bad_time', None
    if t > now + timedelta(minutes=10) or t < now - timedelta(days=MAX_AGE_DAYS):
        return 'time_out_of_range', None
    return None, t


@app.post('/api/v1/catches')
def upload_catches(body: CatchBatch, request: Request, inst: Install = Depends(current_install),
                   s: Session = Depends(db)):
    if not limiter.allow('ip:' + client_ip(request), 120, 60):
        raise HTTPException(429, 'slow down')
    now = utcnow()
    accepted, rejected = [], []
    for c in body.catches:
        reason, t = validate(c, now)
        if reason is None and not limiter.allow('catch:' + inst.id, MAX_CATCHES_PER_HOUR, 3600):
            reason = 'rate_limited'
        if reason:
            rejected.append({'uuid': c.uuid, 'reason': reason})
            continue
        existing = s.get(Catch, c.uuid)
        if existing and existing.install_id != inst.id:
            rejected.append({'uuid': c.uuid, 'reason': 'uuid_taken'})
            continue
        bite = parse_time(c.bite_at) if c.bite_at else None
        row = existing or Catch(uuid=c.uuid, install_id=inst.id, received_at=now)
        row.fish_id, row.waterbody, row.x, row.y = c.fish_id, c.waterbody, c.x, c.y
        row.weight_g, row.length_cm, row.trophy = c.weight_g, c.length_cm, c.badge == 'trophy'
        row.caught_at, row.game_lang, row.client_version = t, c.game_lang, body.client_version
        row.bite_to_catch_s = int((t - bite).total_seconds()) if bite and 0 <= (t - bite).total_seconds() < 3600 else None
        if not existing:
            s.add(row)
        accepted.append(c.uuid)
    inst.last_seen = now
    inst.client_version = body.client_version or inst.client_version
    s.commit()
    return {'accepted': accepted, 'rejected': rejected}


@app.delete('/api/v1/catches/{uuid}')
def delete_catch(uuid: str, inst: Install = Depends(current_install), s: Session = Depends(db)):
    row = s.get(Catch, uuid)
    if row and row.install_id == inst.id:
        s.delete(row)
        s.commit()
    return {'ok': True}


# ---------------------------------------------------------------------------- read API (public, cached)
_cache = {}
CACHE_S = 60


def cached(key, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    if len(_cache) > 2000:
        _cache.clear()
    return val


def _filters(water=None, fish=None, days=None, trophy=None):
    f = [Catch.hidden.is_(False)]
    if water:
        f.append(Catch.waterbody == water)
    if fish:
        f.append(Catch.fish_id == fish)
    if days:
        f.append(Catch.caught_at >= utcnow() - timedelta(days=days))
    if trophy:
        f.append(Catch.trophy.is_(True))
    return and_(*f)


@app.get('/api/v1/meta')
def meta():
    return {'version': VERSION, 'fish': list(gamedata.FISH.values()), 'waterbodies': list(gamedata.WATERBODIES.values())}


@app.get('/api/v1/stats/overview')
def overview(s: Session = Depends(db)):
    def run():
        week = utcnow() - timedelta(days=7)
        total = s.scalar(select(func.count()).select_from(Catch).where(Catch.hidden.is_(False))) or 0
        week_n = s.scalar(select(func.count()).select_from(Catch).where(_filters(days=7))) or 0
        anglers = s.scalar(select(func.count(func.distinct(Catch.install_id))).where(Catch.caught_at >= week)) or 0
        per_water = s.execute(select(Catch.waterbody, func.count()).where(_filters(days=7))
                              .group_by(Catch.waterbody).order_by(func.count().desc())).all()
        trophies = s.execute(select(Catch).where(_filters(trophy=True)).order_by(Catch.caught_at.desc()).limit(25)).scalars()
        return {
            'catches_total': total, 'catches_7d': week_n, 'anglers_7d': anglers,
            'waterbodies_7d': [{'waterbody': w, 'count': n} for w, n in per_water],
            'recent_trophies': [{'fish_id': c.fish_id, 'waterbody': c.waterbody, 'x': c.x, 'y': c.y,
                                 'weight_g': c.weight_g, 'caught_at': c.caught_at.isoformat() + 'Z'} for c in trophies],
        }
    return cached('overview', run)


@app.get('/api/v1/stats/spots')
def spots(water: str = Query(...), fish: str | None = None, days: int | None = Query(30, ge=1, le=3650),
          trophy: bool = False, limit: int = Query(500, le=5000), s: Session = Depends(db)):
    """Catches grouped by map square: the heat map and the hotspot list."""
    if water not in gamedata.WATERBODIES:
        raise HTTPException(404, 'unknown waterbody')

    def run():
        f = _filters(water, fish, days, trophy)
        rows = s.execute(
            select(Catch.x, Catch.y, func.count().label('n'), func.max(Catch.weight_g),
                   func.sum(cast(Catch.trophy, Integer)), func.count(func.distinct(Catch.install_id)))
            .where(f).group_by(Catch.x, Catch.y).order_by(func.count().desc()).limit(limit)).all()
        top = {}
        if rows:
            fr = s.execute(select(Catch.x, Catch.y, Catch.fish_id, func.count()).where(f)
                           .group_by(Catch.x, Catch.y, Catch.fish_id)).all()
            for x, y, fid, n in fr:
                top.setdefault((x, y), []).append((n, fid))
        return {'water': water, 'fish': fish, 'days': days, 'spots': [
            {'x': x, 'y': y, 'count': n, 'max_weight_g': mw, 'trophies': int(tr or 0), 'anglers': a,
             'top_fish': [{'fish_id': fid, 'count': c} for c, fid in sorted(top.get((x, y), []), reverse=True)[:5]]}
            for x, y, n, mw, tr, a in rows]}
    return cached(f'spots:{water}:{fish}:{days}:{trophy}:{limit}', run)


@app.get('/api/v1/stats/fish')
def fish_stats(water: str | None = None, days: int | None = Query(30, ge=1, le=3650), s: Session = Depends(db)):
    """Per species: how often, how big, and its best spot."""
    def run():
        f = _filters(water, None, days)
        rows = s.execute(select(Catch.fish_id, func.count(), func.avg(Catch.weight_g), func.max(Catch.weight_g),
                                func.sum(cast(Catch.trophy, Integer)))
                         .where(f).group_by(Catch.fish_id).order_by(func.count().desc())).all()
        best = {}
        for fid, x, y, w, n in s.execute(select(Catch.fish_id, Catch.x, Catch.y, Catch.waterbody, func.count())
                                         .where(f).group_by(Catch.fish_id, Catch.x, Catch.y, Catch.waterbody)).all():
            if n > best.get(fid, (0,))[0]:
                best[fid] = (n, x, y, w)
        return {'water': water, 'days': days, 'fish': [
            {'fish_id': fid, 'count': n, 'avg_weight_g': round(avg or 0), 'max_weight_g': mx, 'trophies': int(tr or 0),
             'best_spot': {'x': best[fid][1], 'y': best[fid][2], 'waterbody': best[fid][3], 'count': best[fid][0]}
             if fid in best else None}
            for fid, n, avg, mx, tr in rows]}
    return cached(f'fish:{water}:{days}', run)


@app.get('/api/v1/health')
def health():
    return {'ok': True, 'version': VERSION}


# ---------------------------------------------------------------------------- website
app.mount('/maps', StaticFiles(directory=os.path.join(gamedata.DATA_DIR, 'maps'), check_dir=False), name='maps')
app.mount('/static', StaticFiles(directory=WEB_DIR), name='static')


@app.get('/', include_in_schema=False)
def index():
    return FileResponse(os.path.join(WEB_DIR, 'index.html'))


@app.get('/calibrate', include_in_schema=False)
def calibrate():
    return FileResponse(os.path.join(WEB_DIR, 'calibrate.html'))


@app.exception_handler(429)
def too_many(request, exc):
    return JSONResponse({'detail': exc.detail}, status_code=429, headers={'Retry-After': '60'})
