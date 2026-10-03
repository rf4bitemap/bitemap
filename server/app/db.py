"""Database models. PostgreSQL in production, SQLite for local development/tests (DATABASE_URL)."""
import os
from datetime import datetime

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Index, Integer, String, create_engine, inspect,
                        text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///./bitemap.db')

engine = create_engine(
    DATABASE_URL, pool_pre_ping=True,
    connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Install(Base):
    """One anonymous app installation. Only a hash of its token is stored."""
    __tablename__ = 'installs'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen: Mapped[datetime] = mapped_column(DateTime)
    client_version: Mapped[str] = mapped_column(String(32), default='')
    banned: Mapped[bool] = mapped_column(Boolean, default=False)


class Catch(Base):
    __tablename__ = 'catches'
    uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    install_id: Mapped[str] = mapped_column(ForeignKey('installs.id'), index=True)
    fish_id: Mapped[str] = mapped_column(String(48), index=True)
    waterbody: Mapped[str] = mapped_column(String(48))
    x: Mapped[int] = mapped_column(Integer)
    y: Mapped[int] = mapped_column(Integer)
    weight_g: Mapped[int] = mapped_column(Integer)
    length_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    trophy: Mapped[bool] = mapped_column(Boolean, default=False)
    super_trophy: Mapped[bool] = mapped_column(Boolean, default=False)   # implies trophy
    caught_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    bite_to_catch_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    game_lang: Mapped[str | None] = mapped_column(String(4), nullable=True)
    client_version: Mapped[str] = mapped_column(String(32), default='')
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)   # moderation

    __table_args__ = (
        Index('ix_catches_water_time', 'waterbody', 'caught_at'),
        Index('ix_catches_water_spot', 'waterbody', 'x', 'y'),
    )


def init_db():
    Base.metadata.create_all(engine)
    # columns added after the first release (create_all doesn't touch existing tables)
    have = {c['name'] for c in inspect(engine).get_columns('catches')}
    with engine.begin() as con:
        if 'super_trophy' not in have:
            con.execute(text('ALTER TABLE catches ADD COLUMN super_trophy BOOLEAN NOT NULL DEFAULT FALSE'))


def apply_trophy_levels():
    """Trophy / super trophy from fish + weight for all catches - the thresholds can change with game updates."""
    from .gamedata import FISH
    with engine.begin() as con:
        for fid, f in FISH.items():
            t, st = f.get('trophy_g'), f.get('super_trophy_g')
            # no NULL parameters: PostgreSQL can't infer their type
            tr = '(weight_g >= :t)' if t else 'FALSE'
            sup = '(weight_g >= :st)' if t and st else 'FALSE'
            params = {'f': fid, **({'t': t} if t else {}), **({'st': st} if t and st else {})}
            con.execute(text(f'UPDATE catches SET trophy = {tr}, super_trophy = {sup} WHERE fish_id = :f'), params)
