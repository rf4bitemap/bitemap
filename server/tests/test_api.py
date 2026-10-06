import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path}/t.db')
    for m in [m for m in sys.modules if m == 'app' or m.startswith('app.')]:
        del sys.modules[m]
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def now(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat(timespec='seconds')


def catch(**kw):
    c = {'uuid': str(uuid.uuid4()), 'caught_at': now(), 'fish_id': 'lm_b_bass', 'waterbody': 'elk_lake',
         'x': 73, 'y': 48, 'weight_g': 6722, 'length_cm': 79, 'badge': 'trophy', 'coord_age': 4.0}
    c.update(kw)
    return c


def register(client):
    r = client.post('/api/v1/installs', json={'client_version': 'test'})
    assert r.status_code == 200
    return {'Authorization': 'Bearer ' + r.json()['token']}


def test_upload_and_stats(client):
    h = register(client)
    good = [catch(), catch(x=73, y=48, fish_id='b_gill', weight_g=2100, badge=None), catch(x=45, y=60, badge=None)]
    bad = [catch(fish_id='nope'), catch(fish_id='n.pike', weight_g=2100), catch(weight_g=10_000_000), catch(x=5000), catch(caught_at=now(days=-60)),
           catch(waterbody='atlantis'), catch(uuid='not-a-uuid-------------------------xx')]
    r = client.post('/api/v1/catches', json={'client_version': 't', 'catches': good + bad}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert sorted(d['accepted']) == sorted(c['uuid'] for c in good)
    reasons = {x['reason'] for x in d['rejected']}
    assert reasons == {'unknown_fish', 'implausible_weight', 'coords_out_of_range', 'time_out_of_range',
                       'unknown_waterbody', 'bad_uuid', 'not_in_waterbody'}
    sp = client.get('/api/v1/stats/spots', params={'water': 'elk_lake', 'days': 7}).json()['spots']
    # trophy levels come from the weights: bass 6722 g >= 6 kg (trophy), bluegill 2100 g >= 1.7 kg (super trophy)
    assert sp[0]['x'] == 73 and sp[0]['count'] == 2 and sp[0]['trophies'] == 2 and sp[0]['super_trophies'] == 1
    assert {f['fish_id'] for f in sp[0]['top_fish']} == {'lm_b_bass', 'b_gill'}
    sup = client.get('/api/v1/stats/spots', params={'water': 'elk_lake', 'days': 7, 'level': 2}).json()['spots']
    assert [(s['x'], s['count']) for s in sup] == [(73, 1)]
    ov = client.get('/api/v1/stats/overview').json()
    assert ov['catches_7d'] == 3
    assert sorted((t['fish_id'], t['super']) for t in ov['recent_trophies']) == [('b_gill', True), ('lm_b_bass', False), ('lm_b_bass', False)]  # label or not: weight decides
    fs = client.get('/api/v1/stats/fish', params={'water': 'elk_lake'}).json()['fish']
    assert fs[0]['fish_id'] == 'lm_b_bass' and fs[0]['count'] == 2


def test_auth_and_ownership(client):
    c = catch()
    assert client.post('/api/v1/catches', json={'catches': [c]}).status_code == 401
    h1, h2 = register(client), register(client)
    assert client.post('/api/v1/catches', json={'catches': [c]}, headers=h1).json()['accepted'] == [c['uuid']]
    # re-upload by the owner updates (edit), another install cannot take the uuid
    c2 = dict(c, weight_g=7000)
    assert client.post('/api/v1/catches', json={'catches': [c2]}, headers=h1).json()['accepted'] == [c['uuid']]
    r = client.post('/api/v1/catches', json={'catches': [c2]}, headers=h2).json()
    assert r['rejected'][0]['reason'] == 'uuid_taken'
    client.delete(f"/api/v1/catches/{c['uuid']}", headers=h2)  # not the owner: no effect
    assert client.get('/api/v1/stats/overview').json()['catches_total'] >= 0
    assert client.delete(f"/api/v1/catches/{c['uuid']}", headers=h1).status_code == 200


def test_website(client):
    assert client.get('/').status_code == 200
    assert client.get('/api/v1/meta').json()['waterbodies']


def test_min_client_version(client, monkeypatch):
    from app import main
    h = register(client)
    monkeypatch.setattr(main, 'MIN_CLIENT', '0.1.4')
    r = client.post('/api/v1/catches', json={'client_version': '0.1.3', 'catches': [catch()]}, headers=h)
    assert r.status_code == 426
    r = client.post('/api/v1/catches', json={'client_version': '0.1.10', 'catches': [catch()]}, headers=h)
    assert r.status_code == 200 and len(r.json()['accepted']) == 1
    assert client.get('/api/v1/meta').json()['min_client'] == '0.1.4'


def test_trophy_levels_recomputed_on_start(client):
    """Catches stored before (or with other thresholds) get their level from the current weights at start-up."""
    from app.db import SessionLocal, apply_trophy_levels
    from app.main import Catch
    h = register(client)
    c = catch(fish_id='b_gill', weight_g=1300, badge=None)          # 1.2 kg trophy, 1.7 kg super trophy
    assert client.post('/api/v1/catches', json={'client_version': 't', 'catches': [c]}, headers=h).status_code == 200
    with SessionLocal() as s:
        row = s.get(Catch, c['uuid'])
        assert row.trophy and not row.super_trophy
        row.trophy, row.weight_g = False, 1800
        s.commit()
    apply_trophy_levels()
    with SessionLocal() as s:
        row = s.get(Catch, c['uuid'])
        assert row.trophy and row.super_trophy


def test_migration_adds_super_trophy(client):
    from sqlalchemy import inspect, text
    from app.db import engine, init_db
    with engine.begin() as con:
        con.execute(text('ALTER TABLE catches DROP COLUMN super_trophy'))   # the table as the first release made it
    init_db()
    assert 'super_trophy' in {c['name'] for c in inspect(engine).get_columns('catches')}


def test_hotspots_merge_neighbouring_squares(client):
    """One fishing spot covers several neighbouring squares: they form one hotspot, anglers counted once."""
    a, b = register(client), register(client)
    mine = [catch(x=73, y=48), catch(x=74, y=49, badge=None), catch(x=73, y=50, fish_id='b_gill', weight_g=300)]
    theirs = [catch(x=74, y=48, weight_g=1000), catch(x=45, y=60, weight_g=1000)]
    for h, cs in ((a, mine), (b, theirs)):
        assert client.post('/api/v1/catches', json={'client_version': 't', 'catches': cs}, headers=h).status_code == 200
    d = client.get('/api/v1/stats/spots', params={'water': 'elk_lake', 'days': 7}).json()
    assert len(d['spots']) == 5                       # the heat map still gets every square
    hs = d['hotspots']
    assert [(h['count'], h['squares'], h['anglers']) for h in hs] == [(4, 4, 2), (1, 1, 1)]
    assert abs(hs[0]['x'] - 73.5) <= 0.5 and abs(hs[0]['y'] - 48.75) <= 0.75
    assert hs[0]['trophies'] == 2 and hs[0]['top_fish'][0] == {'fish_id': 'lm_b_bass', 'count': 3}
