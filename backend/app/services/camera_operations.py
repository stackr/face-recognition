import threading

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db.session import get_db


class CameraOperations:
    def __init__(self):
        # Fixed-size stripes bound memory while serializing commands per camera.
        self.locks = [threading.Lock() for _ in range(64)]


def camera_mutation(camera_id: int, request: Request, db: Session = Depends(get_db)):
    lock = request.app.state.camera_operations.locks[camera_id % 64]
    lock.acquire()
    try:
        # Authorization may have opened a MariaDB snapshot before a competing
        # upload/edit finished. Begin the actual operation with current rows.
        db.rollback()
        yield
    finally:
        lock.release()
