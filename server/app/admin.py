"""Moderation helpers. Run inside the api container:

    docker compose exec api python -m app.admin stats
    docker compose exec api python -m app.admin top-installs
    docker compose exec api python -m app.admin ban <install_id>      # bans and hides all its catches
    docker compose exec api python -m app.admin unban <install_id>
"""
import sys
from datetime import timedelta

from sqlalchemy import func, select, update

from .db import Catch, Install, SessionLocal, init_db
from .main import utcnow


def main(argv):
    init_db()
    cmd = argv[1] if len(argv) > 1 else 'stats'
    with SessionLocal() as s:
        if cmd == 'stats':
            print('installs:', s.scalar(select(func.count()).select_from(Install)))
            print('catches :', s.scalar(select(func.count()).select_from(Catch)))
            print('last 24h:', s.scalar(select(func.count()).select_from(Catch)
                                        .where(Catch.received_at >= utcnow() - timedelta(days=1))))
        elif cmd == 'top-installs':
            for iid, n in s.execute(select(Catch.install_id, func.count()).where(
                    Catch.received_at >= utcnow() - timedelta(days=1)).group_by(Catch.install_id)
                    .order_by(func.count().desc()).limit(20)):
                print(f'{iid}  {n} catches in 24h')
        elif cmd in ('ban', 'unban') and len(argv) > 2:
            banned = cmd == 'ban'
            s.execute(update(Install).where(Install.id == argv[2]).values(banned=banned))
            s.execute(update(Catch).where(Catch.install_id == argv[2]).values(hidden=banned))
            s.commit()
            print(cmd, argv[2])
        else:
            print(__doc__)


if __name__ == '__main__':
    main(sys.argv)
