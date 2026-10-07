from __future__ import annotations

import csv
import importlib.metadata
import io
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from redis.exceptions import RedisError
from rq.command import send_stop_job_command
from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from . import database as db
from . import worker
from .schemas import CreateRun, SaveCircuit, UpdateCircuit

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    db.init_database()
    try:
        worker.recover_active_runs()
    except (RedisError, OSError):
        logger.warning("Redisに接続できません。既存ジョブの状態は接続回復後に確認します")
    yield


app = FastAPI(title="プラズマ回路シミュレーター", version="0.1.0", lifespan=lifespan)
origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type"])


def get_session():
    with db.session() as session:
        yield session


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def circuit_response(circuit: db.Circuit, detail: bool = True) -> dict[str, Any]:
    result = {
        "id": circuit.id,
        "name": circuit.name,
        "revision": circuit.revision,
        "created_by": circuit.created_by,
        "updated_by": circuit.updated_by,
        "created_at": iso(circuit.created_at),
        "updated_at": iso(circuit.updated_at),
    }
    if detail:
        result["document"] = circuit.document
    return result


def run_response(run: db.SimulationRun, detail: bool = True) -> dict[str, Any]:
    result = {
        "id": run.id,
        "status": run.status,
        "circuit_id": run.circuit_id,
        "circuit_revision": run.circuit_revision,
        "employee_id": run.employee_id,
        "analysis": run.analysis,
        "cancel_requested": run.cancel_requested,
        "created_at": iso(run.created_at),
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
        "error": run.error,
    }
    if detail:
        result["snapshot"] = run.snapshot
        result["runtime_config"] = run.runtime_config
        result["result"] = db.decompress_result(run.result_compressed) if run.status == "succeeded" else None
    return result


def finite_json(value: Any) -> Any:
    try:
        return db.json_copy(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "値は有限の数値またはJSONで表現できる形式にしてください") from exc


def reconcile(run: db.SimulationRun, session: Session) -> db.SimulationRun:
    if run.status not in worker.TERMINAL_STATUSES:
        try:
            worker.reconcile_run(run.id)
        except (RedisError, OSError):
            # Redis outages do not manufacture a terminal state or erase DB history.
            logger.warning("ジョブ状態を確認できません: %s", run.id)
        session.refresh(run)
    return run


@app.get("/api/health")
def health(session: Session = Depends(get_session)):
    session.execute(text("SELECT 1"))
    try:
        worker.redis_connection().ping()
        queue_status = "ok"
    except (RedisError, OSError):
        queue_status = "unavailable"
    try:
        pyspice_version = importlib.metadata.version("PySpice")
    except importlib.metadata.PackageNotFoundError:
        pyspice_version = "unavailable"
    return {"status": "ok" if queue_status == "ok" else "degraded", "database": "ok", "queue": queue_status, "pyspice": pyspice_version}


@app.get("/api/catalog")
def catalog():
    try:
        from .catalog import get_catalog
    except ImportError as exc:
        raise HTTPException(503, "素子カタログを準備中です") from exc
    return get_catalog()


@app.get("/api/presets")
def presets():
    try:
        from .presets import get_presets
    except ImportError as exc:
        raise HTTPException(503, "回路プリセットを準備中です") from exc
    return get_presets()


@app.get("/api/circuits")
def list_circuits(session: Session = Depends(get_session)):
    circuits = session.scalars(select(db.Circuit).order_by(db.Circuit.updated_at.desc())).all()
    return {"circuits": [circuit_response(circuit, detail=False) for circuit in circuits]}


@app.post("/api/circuits", status_code=201)
def create_circuit(request: SaveCircuit, session: Session = Depends(get_session)):
    document = finite_json(request.document.model_dump())
    circuit = db.Circuit(name=document["name"], document=document, created_by=request.employee_id, updated_by=request.employee_id)
    session.add(circuit)
    session.flush()
    session.add(db.CircuitRevision(circuit_id=circuit.id, revision=1, document=db.json_copy(document), employee_id=request.employee_id))
    session.commit()
    return circuit_response(circuit)


@app.get("/api/circuits/{circuit_id}")
def get_circuit(circuit_id: str, session: Session = Depends(get_session)):
    circuit = session.get(db.Circuit, circuit_id)
    if circuit is None:
        raise HTTPException(404, "回路が見つかりません")
    return circuit_response(circuit)


@app.put("/api/circuits/{circuit_id}")
def update_circuit(circuit_id: str, request: UpdateCircuit, session: Session = Depends(get_session)):
    document = finite_json(request.document.model_dump())
    result = session.execute(
        update(db.Circuit)
        .where(db.Circuit.id == circuit_id, db.Circuit.revision == request.expected_revision)
        .values(name=document["name"], document=document, revision=request.expected_revision + 1, updated_by=request.employee_id, updated_at=db.utcnow())
    )
    if result.rowcount != 1:
        session.rollback()
        if session.get(db.Circuit, circuit_id) is None:
            raise HTTPException(404, "回路が見つかりません")
        raise HTTPException(409, "他の保存で回路が更新されています。最新の回路を読み込んでから保存してください")
    session.add(db.CircuitRevision(circuit_id=circuit_id, revision=request.expected_revision + 1, document=document, employee_id=request.employee_id))
    session.commit()
    circuit = session.get(db.Circuit, circuit_id, populate_existing=True)
    return circuit_response(circuit)


@app.post("/api/runs", status_code=202)
def create_run(request: CreateRun, session: Session = Depends(get_session)):
    circuit = session.get(db.Circuit, request.circuit_id, with_for_update=True)
    if circuit is None:
        raise HTTPException(404, "回路が見つかりません")
    if circuit.revision != request.expected_revision:
        raise HTTPException(409, "回路が更新されています。最新の回路を確認してから計算してください")
    run = db.SimulationRun(
        circuit_id=circuit.id,
        circuit_revision=circuit.revision,
        employee_id=request.employee_id,
        snapshot=db.json_copy(circuit.document),
        analysis=finite_json(request.analysis.model_dump()),
        runtime_config=worker.runtime_config(),
    )
    session.add(run)
    session.commit()
    try:
        worker.enqueue_run(run.id)
    except Exception as exc:
        # Persist an honest failure even if Redis was unavailable at enqueue time.
        worker._finish(run.id, "failed", "計算キューに登録できませんでした。Redisの接続状態を確認して再実行してください")
        raise HTTPException(503, {"message": "計算キューに接続できません", "run_id": run.id}) from exc
    session.refresh(run)
    return run_response(run)


@app.get("/api/runs")
def list_runs(circuit_id: str | None = None, session: Session = Depends(get_session)):
    query = select(db.SimulationRun).order_by(db.SimulationRun.created_at.desc()).limit(100)
    if circuit_id:
        query = query.where(db.SimulationRun.circuit_id == circuit_id)
    runs = session.scalars(query).all()
    return {"runs": [run_response(reconcile(run, session), detail=False) for run in runs]}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, session: Session = Depends(get_session)):
    run = session.get(db.SimulationRun, run_id)
    if run is None:
        raise HTTPException(404, "計算履歴が見つかりません")
    return run_response(reconcile(run, session))


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str, session: Session = Depends(get_session)):
    run = session.get(db.SimulationRun, run_id, with_for_update=True)
    if run is None:
        raise HTTPException(404, "計算履歴が見つかりません")
    if run.status in worker.TERMINAL_STATUSES:
        return run_response(run)
    was_queued = run.status == "queued"
    run.cancel_requested = True
    if was_queued:
        run.status = "canceled"
        run.finished_at = db.utcnow()
        run.error = "計算をキャンセルしました"
    session.commit()
    try:
        connection = worker.redis_connection()
        job = Job.fetch(run_id, connection=connection)
        state = job.get_status(refresh=True)
        state = getattr(state, "value", state)
        if state == "started":
            send_stop_job_command(connection, run_id)
            worker._finish(run_id, "canceled", "計算をキャンセルしました")
        elif state in {"queued", "deferred", "scheduled"}:
            job.cancel()
            worker._finish(run_id, "canceled", "計算をキャンセルしました")
        else:
            worker.reconcile_run(run_id)
    except NoSuchJobError:
        worker._finish(run_id, "canceled", "計算をキャンセルしました")
    except (RedisError, OSError):
        # Durable request remains pending if the running process is unreachable.
        logger.warning("停止要求をキューへ送信できません: %s", run_id)
    session.refresh(run)
    return run_response(run)


@app.get("/api/runs/{run_id}/export.csv")
def export_csv(run_id: str, session: Session = Depends(get_session)):
    run = session.get(db.SimulationRun, run_id)
    if run is None:
        raise HTTPException(404, "計算履歴が見つかりません")
    if run.status != "succeeded" or run.result_compressed is None:
        raise HTTPException(409, "計算が正常終了した後にCSVを取得できます")
    result = db.decompress_result(run.result_compressed)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    axis = result.get("axis") or {}
    signals = result.get("signals") or []
    if axis.get("values"):
        writer.writerow([f"{axis.get('name', 'axis')} [{axis.get('unit', '')}]"] + [f"{signal['name']} [{signal.get('unit', '')}]" for signal in signals])
        for index, value in enumerate(axis["values"]):
            writer.writerow([value] + [signal["values"][index] if index < len(signal["values"]) else "" for signal in signals])
    else:
        writer.writerow(["項目", "値"])
        for key, value in result.get("summary", {}).items():
            writer.writerow([key, value])
        for table in result.get("tables", []):
            writer.writerow([])
            if isinstance(table, dict):
                writer.writerow([table.get("name", "")])
                writer.writerow(table.get("columns", []))
                for row in table.get("rows", []):
                    if isinstance(row, dict):
                        writer.writerow([row.get(column, "") for column in table.get("columns", [])])
                    else:
                        writer.writerow(row if isinstance(row, list) else [row])
    return Response(content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="simulation-{run_id}.csv"'})
