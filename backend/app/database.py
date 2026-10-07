"""Durable circuit revisions, immutable run inputs, and compressed results."""
from __future__ import annotations

import json
import os
import uuid
import zlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Circuit(Base):
    __tablename__ = "circuits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    document: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(80))
    updated_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CircuitRevision(Base):
    __tablename__ = "circuit_revisions"
    __table_args__ = (UniqueConstraint("circuit_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    circuit_id: Mapped[str] = mapped_column(ForeignKey("circuits.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict[str, Any]] = mapped_column(JSON)
    employee_id: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SimulationRun(Base):
    __tablename__ = "simulation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    circuit_id: Mapped[str] = mapped_column(ForeignKey("circuits.id"), index=True)
    circuit_revision: Mapped[int] = mapped_column(Integer)
    employee_id: Mapped[str] = mapped_column(String(80))
    # Written once at creation. Subsequent circuit/model edits cannot alter a run.
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON)
    runtime_config: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_compressed: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


engine = None
SessionLocal = None
_engine_pid = None


def configure_database(url: str | None = None) -> None:
    """Production uses PostgreSQL; SQLite is useful for isolated API tests."""
    global engine, SessionLocal, _engine_pid
    if engine is not None:
        engine.dispose()
    database_url = url or os.getenv("DATABASE_URL", "postgresql+psycopg://plasma:plasma@db:5432/plasma")
    options: dict[str, Any] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        if database_url in ("sqlite://", "sqlite:///:memory:"):
            from sqlalchemy.pool import StaticPool
            options["poolclass"] = StaticPool
    engine = create_engine(database_url, **options)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    _engine_pid = os.getpid()


def init_database() -> None:
    if engine is None:
        configure_database()
    Base.metadata.create_all(engine)


def session() -> Session:
    global _engine_pid
    if SessionLocal is None:
        configure_database()
    if _engine_pid != os.getpid():
        # RQ forks a solver child; inherited PostgreSQL sockets belong to the parent.
        engine.dispose(close=False)
        _engine_pid = os.getpid()
    return SessionLocal()


def json_copy(value: Any) -> Any:
    """Reject NaN/Infinity and sever references before durable snapshot writes."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def compress_result(result: dict[str, Any]) -> bytes:
    data = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(data) > int(os.getenv("MAX_RESULT_BYTES", "33554432")):
        raise ValueError("計算結果が保存容量の上限を超えました。保存点数を減らしてください")
    return zlib.compress(data, level=6)


def decompress_result(value: bytes | None) -> dict[str, Any] | None:
    return json.loads(zlib.decompress(value)) if value is not None else None
