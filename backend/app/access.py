"""One ownership rule for models and every history/export derived from them."""
from fastapi import HTTPException
from sqlalchemy import select, true
from sqlalchemy.orm import Session

from . import database as db


def circuit_scope(user: db.Account):
    return true() if user.is_admin else db.Circuit.owner_id == user.id


def benchmark_scope(user: db.Account):
    return true() if user.is_admin else db.Benchmark.owner_id == user.id


def require_circuit(session: Session, circuit_id: str, user: db.Account,
                    *, include_deleted: bool = False, lock: bool = False) -> db.Circuit:
    query = select(db.Circuit).where(db.Circuit.id == circuit_id, circuit_scope(user))
    if not include_deleted:
        query = query.where(db.Circuit.deleted_at.is_(None))
    if lock:
        query = query.with_for_update()
    circuit = session.scalar(query)
    if circuit is None:
        raise HTTPException(404, "モデルが見つかりません")
    return circuit


def require_run(session: Session, run_id: str, user: db.Account, *, lock: bool = False) -> db.SimulationRun:
    query = select(db.SimulationRun).join(db.Circuit).where(db.SimulationRun.id == run_id, circuit_scope(user))
    if lock:
        query = query.with_for_update(of=db.SimulationRun)
    run = session.scalar(query)
    if run is None:
        raise HTTPException(404, "計算履歴が見つかりません")
    return run


def require_study(session: Session, study_id: str, user: db.Account, *, lock: bool = False) -> db.Study:
    query = select(db.Study).join(db.Circuit).where(db.Study.id == study_id, circuit_scope(user))
    if lock:
        query = query.with_for_update(of=db.Study)
    study = session.scalar(query)
    if study is None:
        raise HTTPException(404, "研究が見つかりません")
    return study


def require_benchmark(session: Session, benchmark_id: str, user: db.Account) -> db.Benchmark:
    reference = session.scalar(select(db.Benchmark).where(db.Benchmark.id == benchmark_id, benchmark_scope(user)))
    if reference is None:
        raise HTTPException(404, "参照データが見つかりません")
    return reference
