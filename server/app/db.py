"""Database models. PostgreSQL in production, SQLite for local development/tests (DATABASE_URL)."""
import os
from datetime import datetime

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Index, Integer, String, create_engine)
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
