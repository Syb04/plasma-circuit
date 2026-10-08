from __future__ import annotations

import csv
import importlib.metadata
import io
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from redis.exceptions import RedisError
from rq.command import send_stop_job_command
from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.orm import Session

from . import database as db
from . import worker, studies, benchmarks, analysis_package
from .schemas import (CompareBenchmark, CompareRuns, CreateBenchmark, CreateRun, CreateStudy,
                      DeleteCircuits, EmployeeRequest, ImportPackage, SaveCircuit, UpdateCircuit)

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
def list_circuits(
    q: str = Query("", max_length=200),
    updated_by: str = Query("", max_length=80),
    updated_within_days: int | None = Query(None, ge=1, le=3650),
    sort: Literal["updated_desc", "updated_asc", "name_asc", "name_desc", "created_desc"] = "updated_desc",
    limit: int | None = Query(None, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    # Keep the original unpaginated API available to existing clients. The UI
    # always supplies a bounded limit and never downloads every document.
    circuit = db.Circuit
    description = circuit.document["description"].as_string()
    filters = [circuit.deleted_at.is_(None)]
    if term := q.strip():
        filters.append(or_(*(column.icontains(term, autoescape=True) for column in
                             (circuit.name, description, circuit.created_by, circuit.updated_by))))
    if employee := updated_by.strip():
        filters.append(circuit.updated_by == employee)
    if updated_within_days is not None:
        filters.append(circuit.updated_at >= db.utcnow() - timedelta(days=updated_within_days))
    orders = {
        "updated_desc": circuit.updated_at.desc(),
        "updated_asc": circuit.updated_at.asc(),
        "name_asc": func.lower(circuit.name).asc(),
        "name_desc": func.lower(circuit.name).desc(),
        "created_desc": circuit.created_at.desc(),
    }
    total = session.scalar(select(func.count()).select_from(circuit).where(*filters))
    total_all = session.scalar(select(func.count()).select_from(circuit).where(circuit.deleted_at.is_(None)))
    statement = select(circuit.id, circuit.name, circuit.revision, circuit.created_by,
                       circuit.updated_by, circuit.created_at, circuit.updated_at,
                       description.label("description")).where(*filters).order_by(orders[sort], circuit.id).offset(offset)
    if limit is not None:
        statement = statement.limit(limit)
    circuits = [{**row, "description": row["description"] or "",
                 "created_at": iso(row["created_at"]), "updated_at": iso(row["updated_at"])}
                for row in session.execute(statement).mappings()]
    return {"circuits": circuits, "total": total, "total_all": total_all, "limit": limit, "offset": offset}


@app.post("/api/circuits", status_code=201)
def create_circuit(request: SaveCircuit, session: Session = Depends(get_session)):
    document = finite_json(request.document.model_dump())
    circuit = db.Circuit(name=document["name"], document=document, created_by=request.employee_id, updated_by=request.employee_id)
    session.add(circuit)
    session.flush()
    session.add(db.CircuitRevision(circuit_id=circuit.id, revision=1, document=db.json_copy(document), employee_id=request.employee_id))
    session.commit()
    return circuit_response(circuit)


@app.post("/api/circuits/delete")
def delete_circuits(request: DeleteCircuits, session: Session = Depends(get_session)):
    ids = [target.id for target in request.circuits]
    # A consistent lock order makes overlapping batch deletes serialize safely.
    circuits = {circuit.id: circuit for circuit in session.scalars(
        select(db.Circuit).where(db.Circuit.id.in_(ids)).order_by(db.Circuit.id).with_for_update()
    )}
    for target in request.circuits:
        circuit = circuits.get(target.id)
        if circuit is None:
            raise HTTPException(404, "削除対象のモデルが見つかりません。一覧を更新して選び直してください")
        if circuit.deleted_at is not None or circuit.revision != target.expected_revision:
            raise HTTPException(409, "削除対象に更新・削除されたモデルがあります。一覧を更新して選び直してください。今回は削除していません")
    deleted_at = db.utcnow()
    result = session.execute(update(db.Circuit).where(
        db.Circuit.deleted_at.is_(None),
        or_(*(and_(db.Circuit.id == target.id, db.Circuit.revision == target.expected_revision)
              for target in request.circuits)),
    ).values(deleted_at=deleted_at, deleted_by=request.employee_id))
    if result.rowcount != len(ids):
        session.rollback()
        raise HTTPException(409, "モデルの状態が変わりました。一覧を更新して選び直してください。今回は削除していません")
    session.commit()
    return {"deleted_ids": ids, "deleted_at": iso(deleted_at)}


@app.get("/api/circuits/{circuit_id}")
def get_circuit(circuit_id: str, session: Session = Depends(get_session)):
    circuit = session.get(db.Circuit, circuit_id)
    if circuit is None or circuit.deleted_at is not None:
        raise HTTPException(404, "回路が見つかりません")
    return circuit_response(circuit)


@app.put("/api/circuits/{circuit_id}")
def update_circuit(circuit_id: str, request: UpdateCircuit, session: Session = Depends(get_session)):
    document = finite_json(request.document.model_dump())
    result = session.execute(
        update(db.Circuit)
        .where(db.Circuit.id == circuit_id, db.Circuit.revision == request.expected_revision, db.Circuit.deleted_at.is_(None))
        .values(name=document["name"], document=document, revision=request.expected_revision + 1, updated_by=request.employee_id, updated_at=db.utcnow())
    )
    if result.rowcount != 1:
        session.rollback()
        circuit = session.get(db.Circuit, circuit_id)
        if circuit is None or circuit.deleted_at is not None:
            raise HTTPException(404, "回路が見つかりません")
        raise HTTPException(409, "他の保存で回路が更新されています。最新の回路を読み込んでから保存してください")
    session.add(db.CircuitRevision(circuit_id=circuit_id, revision=request.expected_revision + 1, document=document, employee_id=request.employee_id))
    session.commit()
    circuit = session.get(db.Circuit, circuit_id, populate_existing=True)
    return circuit_response(circuit)


@app.post("/api/runs", status_code=202)
def create_run(request: CreateRun, session: Session = Depends(get_session)):
    circuit = session.get(db.Circuit, request.circuit_id, with_for_update=True)
    if circuit is None or circuit.deleted_at is not None:
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


def require_study(study_id: str, session: Session, lock: bool = False) -> db.Study:
    study = session.get(db.Study, study_id, with_for_update=lock)
    if study is None:
        raise HTTPException(404, "Study not found")
    return study


def reconcile_study(study: db.Study, session: Session) -> None:
    for _, attempts in studies.case_records(session, study.id):
        if attempts:
            reconcile(attempts[-1][1], session)


@app.post("/api/studies", status_code=202)
def create_study(request: CreateStudy, session: Session = Depends(get_session)):
    # The lock serializes snapshotting with the revision CAS used by circuit saves.
    circuit = session.get(db.Circuit, request.circuit_id, with_for_update=True)
    if circuit is None or circuit.deleted_at is not None:
        raise HTTPException(404, "Circuit not found")
    if circuit.revision != request.expected_revision:
        raise HTTPException(409, "Circuit revision changed; reload the circuit before creating a study")
    axes, analysis = finite_json([a.model_dump() for a in request.axes]), finite_json(request.analysis.model_dump())
    try:
        cases = studies.expand_cases(circuit.document, analysis, axes)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    study = db.Study(name=request.name, circuit_id=circuit.id, circuit_revision=circuit.revision,
                     employee_id=request.employee_id, snapshot=db.json_copy(circuit.document), analysis=analysis, axes=axes)
    session.add(study)
    session.flush()
    runtime = worker.runtime_config()
    runs = []
    for index, (coordinates, document, case_analysis) in enumerate(cases):
        case = db.StudyCase(study_id=study.id, case_index=index, coordinates=coordinates)
        session.add(case)
        session.flush()
        runs.append(studies.new_attempt(session, study, case, 1, request.employee_id, document, case_analysis, runtime))
    # Every case exists before any queue submission. Queue failures are case
    # failures retained in history, not partial database batches.
    session.commit()
    studies.enqueue_cases([run.id for run in runs])
    session.expire_all()
    return studies.response(session.get(db.Study, study.id), session)


@app.get("/api/studies")
def list_studies(circuit_id: str | None = None, session: Session = Depends(get_session)):
    query = select(db.Study).order_by(db.Study.created_at.desc()).limit(100)
    if circuit_id:
        query = query.where(db.Study.circuit_id == circuit_id)
    result = []
    for study in session.scalars(query).all():
        reconcile_study(study, session)
        result.append(studies.response(study, session, detail=False))
    return {"studies": result}


@app.get("/api/studies/{study_id}")
def get_study(study_id: str, session: Session = Depends(get_session)):
    study = require_study(study_id, session)
    reconcile_study(study, session)
    return studies.response(study, session)


@app.post("/api/studies/{study_id}/cancel")
def cancel_study(study_id: str, session: Session = Depends(get_session)):
    study = require_study(study_id, session, lock=True)
    study.cancel_requested = True
    session.commit()
    ids = [attempts[-1][1].id for _, attempts in studies.case_records(session, study.id) if attempts and attempts[-1][1].status not in worker.TERMINAL_STATUSES]
    for run_id in ids:
        cancel_run(run_id, session)
    return studies.response(study, session)


@app.post("/api/studies/{study_id}/resume", status_code=202)
def resume_study(study_id: str, request: EmployeeRequest, session: Session = Depends(get_session)):
    study = require_study(study_id, session, lock=True)
    # Each retry is a new run, preserving original failures and their diagnostics.
    runtime, run_ids = worker.runtime_config(), []
    for case, attempts in studies.case_records(session, study.id):
        if not attempts:
            continue
        last_attempt, previous = attempts[-1]
        if previous.status not in {"failed", "timed_out", "canceled"}:
            continue
        new_run = studies.new_attempt(session, study, case, last_attempt.number + 1, request.employee_id,
                                      db.json_copy(previous.snapshot), db.json_copy(previous.analysis), runtime)
        run_ids.append(new_run.id)
    if not run_ids:
        raise HTTPException(409, "No failed, timed-out, or canceled cases are available to retry")
    study.cancel_requested = False
    session.commit()
    studies.enqueue_cases(run_ids)
    session.expire_all()
    return studies.response(session.get(db.Study, study_id), session)


@app.get("/api/studies/{study_id}/export.csv")
def export_study_csv(study_id: str, session: Session = Depends(get_session)):
    study = require_study(study_id, session)
    reconcile_study(study, session)
    records, summary_keys = [], set()
    for case, attempts in studies.case_records(session, study.id):
        attempt, run = attempts[-1]
        result = db.decompress_result(run.result_compressed) or {}
        summary = {key: value for key, value in result.get("summary", {}).items() if isinstance(value, (int, float, str, bool)) or value is None}
        summary_keys.update(summary)
        records.append((case, attempt, run, summary))
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    axis_paths, summary_keys = [axis["path"] for axis in study.axes], sorted(summary_keys)
    writer.writerow(["case_index", "run_id", "attempt", "status", "error", *axis_paths, *summary_keys])
    for case, attempt, run, summary in records:
        writer.writerow([case.case_index, run.id, attempt.number, run.status, run.error or "",
                         *(case.coordinates[path] for path in axis_paths), *(summary.get(key, "") for key in summary_keys)])
    return Response(content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="study-{study_id}.csv"'})


@app.post("/api/compare")
def compare(request: CompareRuns, session: Session = Depends(get_session)):
    if len(set(request.run_ids)) != len(request.run_ids):
        raise HTTPException(422, "Select distinct runs")
    records = []
    for run_id in request.run_ids:
        run = session.get(db.SimulationRun, run_id)
        if run is None:
            raise HTTPException(404, f"Run not found: {run_id}")
        records.append(run_response(reconcile(run, session)))
    return benchmarks.compare_runs(records, request.phase_align)


def benchmark_response(reference: db.Benchmark) -> dict:
    return {**reference.reference, "id": reference.id, "created_at": reference.created_at.isoformat()}


@app.post("/api/benchmarks", status_code=201)
def create_benchmark(request: CreateBenchmark, session: Session = Depends(get_session)):
    try:
        parsed = benchmarks.parse_reference(finite_json(request.model_dump()))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    reference = db.Benchmark(name=request.name, reference=parsed)
    session.add(reference)
    session.commit()
    return benchmark_response(reference)


@app.get("/api/benchmarks")
def list_benchmarks(session: Session = Depends(get_session)):
    references = session.scalars(select(db.Benchmark).order_by(db.Benchmark.created_at.desc()).limit(100)).all()
    return {"benchmarks": [benchmark_response(reference) for reference in references]}


@app.get("/api/benchmarks/{benchmark_id}")
def get_benchmark(benchmark_id: str, session: Session = Depends(get_session)):
    reference = session.get(db.Benchmark, benchmark_id)
    if reference is None:
        raise HTTPException(404, "Reference not found")
    return benchmark_response(reference)


@app.post("/api/benchmarks/{benchmark_id}/compare")
def compare_benchmark(benchmark_id: str, request: CompareBenchmark, session: Session = Depends(get_session)):
    reference = session.get(db.Benchmark, benchmark_id)
    run = session.get(db.SimulationRun, request.run_id)
    if reference is None or run is None:
        raise HTTPException(404, "Reference or run not found")
    reconcile(run, session)
    if run.status != "succeeded" or run.result_compressed is None:
        raise HTTPException(409, "A completed run is required for reference comparison")
    return {"run_id": run.id, "benchmark_id": reference.id,
            **benchmarks.compare_reference(db.decompress_result(run.result_compressed), reference.reference, run.analysis)}


@app.get("/api/runs/{run_id}/package")
def export_package(run_id: str, session: Session = Depends(get_session)):
    run = session.get(db.SimulationRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    try:
        return analysis_package.export_run(reconcile(run, session))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/packages/import", status_code=201)
def import_package(request: ImportPackage, session: Session = Depends(get_session)):
    try:
        package = analysis_package.verify_package(request.package)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    document = db.json_copy(package["input"]["document"])
    circuit = db.Circuit(name=document["name"], document=document,
                         created_by=request.employee_id, updated_by=request.employee_id)
    session.add(circuit)
    session.flush()
    session.add(db.CircuitRevision(circuit_id=circuit.id, revision=1, document=db.json_copy(document), employee_id=request.employee_id))
    provenance = db.PackageImport(circuit_id=circuit.id, employee_id=request.employee_id,
                                  original_run_id=package["original_run_id"], package=package)
    session.add(provenance)
    session.commit()
    return {"circuit": circuit_response(circuit), "analysis": package["input"]["analysis"],
            "provenance": {"id": provenance.id, "original_run_id": provenance.original_run_id,
                           "original_status": package.get("original_status"), "hashes": package["hashes"],
                           "runtime_config": package["runtime_config"]},
            "verification": {"hashes_verified": True, "authenticity_verified": False},
            "requires_recalculation": True, "result": None}
