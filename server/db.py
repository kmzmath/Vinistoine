"""
Persistência simples: usuários (apenas nickname) e decks salvos.
Usa SQLite por padrão; se DATABASE_URL estiver setada, usa-a (Postgres em produção, ex.).
"""
from __future__ import annotations
import os
import json
from sqlalchemy import create_engine, String, Integer, Text, ForeignKey, DateTime, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, Session
from datetime import datetime
from typing import Optional


DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./cardgame.db")
# Render às vezes manda postgres:// ao invés de postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, echo=False, future=True,
                       connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nickname: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    selected_portrait: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    decks: Mapped[list["Deck"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Deck(Base):
    __tablename__ = "decks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(60))
    cards_json: Mapped[str] = mapped_column(Text)  # lista de card_ids
    cover_card_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    user: Mapped[User] = relationship(back_populates="decks")

    def card_ids(self) -> list[str]:
        return json.loads(self.cards_json)


def init_db():
    Base.metadata.create_all(engine)
    # Migração leve para bancos SQLite/Postgres já existentes. create_all não
    # adiciona colunas novas, então garantimos o campo usado pela seleção de
    # portrait sem exigir Alembic neste projeto pequeno.
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(users)")} if DATABASE_URL.startswith("sqlite") else set()
        if DATABASE_URL.startswith("sqlite"):
            if "selected_portrait" not in cols:
                conn.exec_driver_sql("ALTER TABLE users ADD COLUMN selected_portrait VARCHAR(120)")
            deck_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(decks)")}
            if "cover_card_id" not in deck_cols:
                conn.exec_driver_sql("ALTER TABLE decks ADD COLUMN cover_card_id VARCHAR(64)")
        else:
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS selected_portrait VARCHAR(120)"))
            conn.execute(text("ALTER TABLE decks ADD COLUMN IF NOT EXISTS cover_card_id VARCHAR(64)"))


def get_session() -> Session:
    return Session(engine)
