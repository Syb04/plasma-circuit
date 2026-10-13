"""RQ initialization must preserve the worker's indefinite PubSub read."""
from app.worker import SimulationWorker, redis_connection


def test_worker_keeps_explicit_blocking_connection_through_rq_initialization():
    connection = redis_connection(blocking=True)
    worker = SimulationWorker(["isolated-connection-test"], connection=connection, prepare_for_work=False)
    assert worker.connection.connection_pool.connection_kwargs["socket_timeout"] is None


def test_nonblocking_pool_retains_rq_required_dequeue_deadline():
    connection = redis_connection()
    worker = SimulationWorker(["isolated-connection-test"], connection=connection, prepare_for_work=False)
    assert worker.connection.connection_pool.connection_kwargs["socket_timeout"] >= worker.connection_timeout
