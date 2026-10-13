#!/usr/bin/env python3
"""Real Redis idle PubSub check; run inside the disposable API container."""
import json
import threading
import time
import uuid

from rq import Queue
from app.worker import SimulationWorker, redis_connection


def main():
    delivered=threading.Event()
    marker='idle-probe-'+uuid.uuid4().hex
    class ProbeWorker(SimulationWorker):
        def handle_payload(self,message):
            if message['data']==marker.encode():
                delivered.set()
    connection=redis_connection(blocking=True)
    worker=ProbeWorker([Queue('idle-probe-'+uuid.uuid4().hex,connection=connection)],
                       connection=connection,worker_ttl=3,prepare_for_work=False)
    worker.subscribe()
    try:
        idle_seconds=worker.connection_timeout+2
        time.sleep(idle_seconds)
        assert worker.pubsub_thread.is_alive(), 'Idle command listener died'
        connection.publish(worker.pubsub_channel_name,marker)
        assert delivered.wait(3), 'Command was not delivered after idle'
        print(json.dumps({'passed':True,'idle_seconds':idle_seconds,'rq_default_socket_deadline':worker.connection_timeout,
                          'listener_alive':True,'message_delivered':True}))
    finally:
        worker.unsubscribe()
        connection.close()


if __name__=='__main__':main()
