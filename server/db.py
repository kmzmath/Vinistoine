"""
Persistência simples: usuários (apenas nickname) e decks salvos.
Usa SQLite por padrão; se DATABASE_URL estiver setada, usa-a (Postgres em produção, ex.).
"""
from __future__ import annotations
import os
import json
from sqlalchemy import create_engine, String, Integer, Text, ForeignKey, DateTime, func
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    decks: Mapped[list["Deck"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Deck(Base):
    __tablename__ = "decks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(60))
    cards_json: Mapped[str] = mapped_column(Text)  # lista de card_ids
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    user: Mapped[User] = relationship(back_populates="decks")

    def card_ids(self) -> list[str]:
        return json.loads(self.cards_json)


def init_db():
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
