"""RQ child-process execution and durable status reconciliation."""
from __future__ import annotations

import hashlib
import importlib.metadata
import os
from datetime import timezone
from pathlib import Path
from typing import Any

from redis import Redis
from rq import Queue, Worker
from rq.job import Job

from . import database as db

TERMINAL_STATUSES = {"succeeded", "failed", "canceled", "timed_out"}


def job_timeout() -> int:
    return max(10, min(3600, int(os.getenv("JOB_TIMEOUT_SECONDS", "180"))))


def redis_connection(*, blocking: bool = False) -> Redis:
    return Redis.from_url(
        os.getenv("REDIS_URL", "redis://redis:6379/0"),
        socket_connect_timeout=2,
        # RQ's blocking dequeue waits longer than web requests are allowed to wait.
        socket_timeout=None if blocking else 3,
    )


def get_queue() -> Queue:
    return Queue(os.getenv("RQ_QUEUE", "simulations"), connection=redis_connection(), default_timeout=job_timeout())


def runtime_config() -> dict[str, Any]:
    versions = {}
    for package in ("PySpice", "numpy", "scipy", "rq"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    # Include source fingerprints without importing heavyweight numerical modules.
    fingerprints = {}
    source_root = Path(__file__).parent
    for path in sorted(source_root.rglob("*")):
        if path.is_file() and (path.suffix == ".py" or "data" in path.relative_to(source_root).parts):
            fingerprints[path.relative_to(source_root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"job_timeout_seconds": job_timeout(), "packages": versions, "implementation_sha256": fingerprints}


def _finish(run_id: str, status: str, error: str | None = None) -> None:
    with db.session() as session:
        run = session.get(db.SimulationRun, run_id, with_for_update=True)
        if run is None or run.status in TERMINAL_STATUSES:
            return
        run.status = "canceled" if run.cancel_requested else status
        run.error = "計算をキャンセルしました" if run.cancel_requested else error
        run.finished_at = db.utcnow()
        session.commit()


def execute_run(run_id: str) -> None:
    """Called in RQ's forked work-horse, never in the web-server process."""
    with db.session() as session:
        run = session.get(db.SimulationRun, run_id, with_for_update=True)
        if run is None or run.status in TERMINAL_STATUSES:
            return
        if run.cancel_requested:
            run.status = "canceled"
            run.finished_at = db.utcnow()
            session.commit()
            return
        run.status = "running"
        run.started_at = db.utcnow()
        document = db.json_copy(run.snapshot)
        analysis = db.json_copy(run.analysis)
        session.commit()
    try:
        from .simulation import execute_simulation
        result = execute_simulation(document, analysis)
        compressed = db.compress_result(result)
        with db.session() as session:
            run = session.get(db.SimulationRun, run_id, with_for_update=True)
            if run is None or run.status in TERMINAL_STATUSES:
                return
            run.finished_at = db.utcnow()
            if run.cancel_requested:
                run.status = "canceled"
                run.error = "計算をキャンセルしました"
            else:
                run.status = "succeeded"
                run.result_compressed = compressed
            session.commit()
    except Exception as exc:
        timeout = "JobTimeout" in type(exc).__name__
        details = str(exc) or type(exc).__name__
        logs = getattr(exc, "logs", None)
        if logs:
            details += "\n" + "\n".join(str(line) for line in logs)
        _finish(run_id, "timed_out" if timeout else "failed", details[:40000])
        raise


def on_failure(job: Job, connection: Redis, exception_type, exception_value, traceback) -> None:
    """Also runs for worker-level timeout/crash paths where the child cannot commit."""
    timeout = "JobTimeout" in getattr(exception_type, "__name__", "")
    message = "計算が制限時間を超えました" if timeout else "計算ワーカーでエラーが発生しました: " + str(exception_value)
    _finish(str(job.args[0]), "timed_out" if timeout else "failed", message[:40000])


def on_stopped(job: Job, connection: Redis) -> None:
    _finish(str(job.args[0]), "canceled", "計算を停止しました")


def on_work_horse_killed(job: Job, retpid: int, ret_val: int, rusage) -> None:
    _finish(str(job.args[0]), "failed", f"計算プロセスが異常終了しました（終了コード {ret_val}）")


def enqueue_run(run_id: str, queue: Queue | None = None) -> Job:
    queue = queue or get_queue()
    return queue.enqueue(
        execute_run,
        run_id,
        job_id=run_id,
        job_timeout=job_timeout(),
        result_ttl=86400,
        failure_ttl=604800,
        on_failure=on_failure,
        on_stopped=on_stopped,
    )


def reconcile_run(run_id: str, queue: Queue | None = None) -> None:
    """Redis completion state repairs PostgreSQL after hard process/host failures."""
    from rq.exceptions import NoSuchJobError
    with db.session() as session:
        run = session.get(db.SimulationRun, run_id)
        if run is None or run.status in TERMINAL_STATUSES:
            return
        created = run.created_at.replace(tzinfo=timezone.utc) if run.created_at.tzinfo is None else run.created_at
        started = run.started_at
        timeout_seconds = run.runtime_config.get("job_timeout_seconds", job_timeout())
    queue = queue or get_queue()
    try:
        job = Job.fetch(run_id, connection=queue.connection)
        state = job.get_status(refresh=True)
        state = getattr(state, "value", state)
    except NoSuchJobError:
        # Allow the brief DB-commit -> enqueue interval before declaring a lost job.
        if (db.utcnow() - created).total_seconds() > 60:
            _finish(run_id, "failed", "計算キューにジョブがありません。保存済み条件で再実行してください")
        return
    if state == "failed":
        error = job.exc_info or "計算ワーカーが異常終了しました"
        timed_out = "JobTimeoutException" in error
        _finish(run_id, "timed_out" if timed_out else "failed", error[-40000:])
    elif state in {"stopped", "canceled"}:
        _finish(run_id, "canceled", "計算を停止しました")
    elif state == "finished":
        # Worker commits the result before returning to RQ; absence means a lost write.
        _finish(run_id, "failed", "計算は終了しましたが結果の保存を確認できませんでした")
    elif started is not None:
        started = started.replace(tzinfo=timezone.utc) if started.tzinfo is None else started
        if (db.utcnow() - started).total_seconds() > timeout_seconds + 60:
            _finish(run_id, "failed", "計算ワーカーの応答が途絶えました。保存済み条件で再実行してください")


def recover_active_runs() -> None:
    with db.session() as session:
        ids = session.query(db.SimulationRun.id).filter(db.SimulationRun.status.in_(["queued", "running"])).all()
    queue = get_queue()
    for (run_id,) in ids:
        reconcile_run(run_id, queue)


def main() -> None:
    db.init_database()
    # Connections inherited across fork must not reuse a parent process's sockets.
    db.engine.dispose()
    connection = redis_connection(blocking=True)
    queue = Queue(os.getenv("RQ_QUEUE", "simulations"), connection=connection, default_timeout=job_timeout())
    recover_active_runs()
    worker = Worker([queue], connection=connection, maintenance_interval=30, work_horse_killed_handler=on_work_horse_killed)
    worker.work(with_scheduler=False, logging_level="INFO")


if __name__ == "__main__":
    main()
