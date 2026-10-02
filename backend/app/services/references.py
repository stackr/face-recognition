"""Durable reconciliation of SQL, Qdrant, private files and the worker cache."""

import base64
import logging
import os
import re
import shutil
import threading
import uuid
from datetime import UTC, timedelta

from fastapi import HTTPException
from qdrant_client import models as qm
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.face_data import MODEL_VERSION
from app.models import AuditLog, FaceCleanupJob, GalleryState, Person, PersonFace, PersonPermission
from app.models.foundation import utc_now
from app.services.face_search import decrypt_embedding, encrypt_embedding, ensure_collection


def bump(db):
    state = db.get(GalleryState, 1)
    if state is None:
        raise RuntimeError("Gallery migration required")
    state.revision += 1
    return state.revision


def audit(db, user_id, action, person_id):
    db.add(
        AuditLog(user_id=user_id, action=action, resource_type="person", resource_id=str(person_id))
    )


def allowed_person_ids(db, user):
    query = select(Person.id).where(Person.deleting.is_(False))
    if user.role != "admin":
        query = query.join(PersonPermission).where(PersonPermission.user_id == user.id)
    return list(db.scalars(query))


class ReferenceService:
    def __init__(self, settings, engine, vectors, worker):
        self.settings, self.engine, self.vectors, self.worker = settings, engine, vectors, worker
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.thread = None

    def path(self, name):
        if not name or not re.fullmatch(r"[a-f0-9]{32}\.jpg", name):
            raise ValueError("Invalid private reference path")
        path = self.settings.reference_dir / name
        if path.resolve().parent != self.settings.reference_dir.resolve() or path.is_symlink():
            raise ValueError("Invalid private reference path")
        return path

    def enqueue(self, db, person_id):
        key = f"person:{person_id}"
        job = db.scalar(select(FaceCleanupJob).where(FaceCleanupJob.job_key == key))
        if job is None:
            job = FaceCleanupJob(job_key=key, action="reconcile", person_id=person_id)
            db.add(job)
        else:
            job.status, job.next_attempt_at, job.completed_at = "pending", utc_now(), None
        return job

    def upload(self, person_id, analyzed, user_id):
        with self.lock, Session(self.engine) as db:
            person = db.get(Person, person_id)
            if person is None or person.deleting:
                raise HTTPException(404, "Person unavailable")
            faces = list(db.scalars(select(PersonFace).where(PersonFace.person_id == person_id)))
            if len(faces) >= self.settings.reference_faces_per_person:
                raise HTTPException(409, "Reference face limit reached")
            if analyzed["model_version"] != MODEL_VERSION:
                raise HTTPException(503, "Face model version mismatch")
            jpeg = base64.b64decode(analyzed["aligned_jpeg"], validate=True)
            if len(jpeg) > 200_000 or not jpeg.startswith(b"\xff\xd8"):
                raise HTTPException(503, "Invalid aligned reference")
            directory = self.settings.reference_dir
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
            used = sum(path.stat().st_size for path in directory.glob("*.jpg"))
            if (
                used + len(jpeg) > self.settings.reference_storage_max_mb * 1024**2
                or shutil.disk_usage(directory).free < 500 * 1024**2
            ):
                raise HTTPException(409, "Reference storage limit reached")
            now = utc_now()
            name = f"{uuid.uuid4().hex}.jpg"
            path = self.path(name)
            face = PersonFace(
                person_id=person_id,
                image_path=name,
                embedding_id=str(uuid.uuid4()),
                model_version=MODEL_VERSION,
                embedding_encrypted=encrypt_embedding(
                    analyzed["embedding"], self.settings.rtsp_encryption_key.get_secret_value()
                ),
                quality=analyzed["quality"]["quality"],
                state="pending",
                image_expires_at=now + timedelta(days=self.settings.reference_image_retention_days),
                embedding_expires_at=now
                + timedelta(days=self.settings.reference_embedding_retention_days),
            )
            try:
                with path.open("xb") as stream:
                    os.chmod(path, 0o600)
                    stream.write(jpeg)
                    stream.flush()
                    os.fsync(stream.fileno())
                db.add(face)
                db.flush()
                face_id = face.id
                self.enqueue(db, person_id)
                bump(db)
                audit(db, user_id, "person.face.upload", person_id)
                db.commit()
            except Exception:
                db.rollback()
                path.unlink(missing_ok=True)
                raise
            synchronized = self.reconcile(person_id)
            with Session(self.engine) as fresh:
                stored = fresh.get(PersonFace, face_id)
                return face_public(stored, self.settings), synchronized

    def acknowledge(self, db):
        revision = db.get(GalleryState, 1).revision
        reply = self.worker.request(
            "POST", "/internal/gallery/reload", json={"revision": revision}
        ).json()
        if reply.get("revision", -1) < revision:
            raise RuntimeError("Worker gallery acknowledgement missing")

    def reconcile(self, person_id):
        with self.lock, Session(self.engine) as db:
            job = db.scalar(
                select(FaceCleanupJob).where(FaceCleanupJob.job_key == f"person:{person_id}")
            )
            if job is None or job.status == "done":
                return True
            newly_ready = []
            try:
                # SQL exclusions are already committed. ACK before physical deletion.
                self.acknowledge(db)
                ensure_collection(self.vectors, self.settings.face_collection)
                person = db.get(Person, person_id)
                faces = list(
                    db.scalars(select(PersonFace).where(PersonFace.person_id == person_id))
                )
                now = utc_now()
                for face in faces:
                    remove = person is None or person.deleting or face.state == "deleting"
                    expired = face.embedding_expires_at <= now or face.state == "expired"
                    if remove or expired:
                        self.vectors.delete(
                            self.settings.face_collection,
                            points_selector=qm.PointIdsList(points=[face.embedding_id]),
                            wait=True,
                        )
                        face.embedding_encrypted = None
                        face.state = "expired" if not remove else "deleting"
                    elif face.embedding_encrypted:
                        vector = decrypt_embedding(
                            face.embedding_encrypted,
                            self.settings.rtsp_encryption_key.get_secret_value(),
                        )
                        self.vectors.upsert(
                            self.settings.face_collection,
                            points=[
                                qm.PointStruct(
                                    id=face.embedding_id,
                                    vector=vector.tolist(),
                                    payload={
                                        "person_id": person_id,
                                        "face_id": face.id,
                                        "model_version": MODEL_VERSION,
                                        "quality": face.quality,
                                        "enabled": person.enabled,
                                        "created_at": face.created_at.isoformat(),
                                    },
                                )
                            ],
                            wait=True,
                        )
                        if face.state == "pending":
                            face.state = "ready"
                            newly_ready.append(face.id)
                    if face.image_path and (remove or face.image_expires_at <= now):
                        self.path(face.image_path).unlink(missing_ok=True)
                        face.image_path = None
                    if remove or (not face.image_path and not face.embedding_encrypted):
                        db.delete(face)
                if person and person.deleting:
                    db.execute(
                        delete(PersonPermission).where(PersonPermission.person_id == person_id)
                    )
                    db.delete(person)
                if newly_ready:
                    bump(db)
                db.commit()
                self.acknowledge(db)
                job.status, job.last_error, job.completed_at = "done", None, utc_now()
                db.commit()
                return True
            except Exception as exc:
                db.rollback()
                # A successful upsert without a worker ACK remains explicitly pending.
                for face_id in newly_ready:
                    face = db.get(PersonFace, face_id)
                    if face and face.state == "ready":
                        face.state = "pending"
                        bump(db)
                job = db.scalar(
                    select(FaceCleanupJob).where(FaceCleanupJob.job_key == f"person:{person_id}")
                )
                job.status = "pending"
                job.attempts += 1
                job.last_error = type(exc).__name__
                job.next_attempt_at = utc_now() + timedelta(
                    seconds=min(300, 2 ** min(job.attempts, 8))
                )
                db.commit()
                logging.getLogger("cctv").warning(
                    "Reference synchronization pending; person=%s type=%s",
                    person_id,
                    type(exc).__name__,
                )
                return False

    def cleanup(self):
        with self.lock, Session(self.engine) as db:
            now = utc_now()
            due = list(
                db.scalars(
                    select(PersonFace)
                    .where(
                        (
                            (PersonFace.image_path.is_not(None))
                            & (PersonFace.image_expires_at <= now)
                        )
                        | (
                            (PersonFace.embedding_encrypted.is_not(None))
                            & (PersonFace.embedding_expires_at <= now)
                        )
                    )
                    .limit(200)
                )
            )
            for face in due:
                if face.embedding_expires_at <= now and face.state not in {"expired", "deleting"}:
                    face.state = "expired"
                    bump(db)
                self.enqueue(db, face.person_id)
                audit(db, None, "person.face.retention", face.person_id)
            db.commit()
            db.execute(
                delete(FaceCleanupJob).where(
                    FaceCleanupJob.status == "done",
                    FaceCleanupJob.completed_at < now - timedelta(days=7),
                )
            )
            # Recover files left by a process crash between file write and SQL commit.
            known = set(
                db.scalars(select(PersonFace.image_path).where(PersonFace.image_path.is_not(None)))
            )
            cutoff = (now - timedelta(hours=1)).replace(tzinfo=UTC).timestamp()
            for path in self.settings.reference_dir.glob("*.jpg"):
                if path.name not in known and path.stat().st_mtime < cutoff:
                    self.path(path.name).unlink(missing_ok=True)
            db.commit()
            ids = list(
                db.scalars(
                    select(FaceCleanupJob.person_id)
                    .where(
                        FaceCleanupJob.status == "pending",
                        FaceCleanupJob.next_attempt_at <= utc_now(),
                    )
                    .order_by(FaceCleanupJob.id)
                    .limit(20)
                )
            )
        for person_id in ids:
            if self.cancel.is_set():
                break
            self.reconcile(person_id)

    def start(self):
        def run():
            while not self.cancel.is_set():
                try:
                    self.cleanup()
                except Exception as exc:
                    logging.getLogger("cctv").warning(
                        "Reference maintenance failed; type=%s", type(exc).__name__
                    )
                self.cancel.wait(self.settings.reference_cleanup_interval_seconds)

        self.thread = threading.Thread(target=run, daemon=True, name="reference-cleanup")
        self.thread.start()

    def close(self):
        self.cancel.set()
        if self.thread:
            self.thread.join()


def face_public(face, settings):
    now = utc_now()
    return {
        "id": face.id,
        "person_id": face.person_id,
        "quality": face.quality,
        "state": "expired" if face.embedding_expires_at <= now else face.state,
        "image_available": bool(face.image_path and face.image_expires_at > now),
        "image_expires_at": face.image_expires_at.isoformat() + "Z",
        "embedding_expires_at": face.embedding_expires_at.isoformat() + "Z",
        "created_at": face.created_at.isoformat() + "Z",
    }
