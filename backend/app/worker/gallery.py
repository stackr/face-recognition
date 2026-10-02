"""The analysis worker owns the reference cache; SQL is the eligibility authority."""

import threading

from qdrant_client import QdrantClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.face_data import MODEL_VERSION
from app.db.session import make_engine
from app.models import GalleryState, Person, PersonFace
from app.models.foundation import utc_now
from app.services.face_search import (
    MemoryFaceSearch,
    QdrantFaceSearch,
    Reference,
    decrypt_embedding,
    ensure_collection,
)


class ReferenceGallery:
    def __init__(self, settings, engine=None, client=None):
        self.settings = settings
        self.engine = engine if engine is not None else make_engine(settings)
        self.client = (
            client
            if client is not None
            else QdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key.get_secret_value() or None,
                timeout=2,
                check_compatibility=False,
            )
        )
        self.owned = engine is None
        self.lock = threading.RLock()
        self.revision = 0
        self.key = None
        self.references = []
        self.memory = MemoryFaceSearch()
        self.qdrant = QdrantFaceSearch(self.client, settings.face_collection)
        ensure_collection(self.client, settings.face_collection)
        self.snapshot()

    def snapshot(self):
        with self.lock, Session(self.engine) as db:
            state = db.get(GalleryState, 1)
            if state is None:
                raise RuntimeError("Gallery migration required")
            eligible = (
                select(PersonFace, Person)
                .join(Person, Person.id == PersonFace.person_id)
                .where(
                    Person.enabled.is_(True),
                    Person.deleting.is_(False),
                    PersonFace.state == "ready",
                    PersonFace.embedding_encrypted.is_not(None),
                    PersonFace.embedding_expires_at > utc_now(),
                    PersonFace.model_version == MODEL_VERSION,
                )
                .order_by(PersonFace.id)
            )
            identifiers = tuple(db.scalars(eligible.with_only_columns(PersonFace.embedding_id)))
            key = (state.revision, identifiers)
            if key != self.key:
                rows = db.execute(eligible).all()
                self.references = [
                    Reference(
                        face.id,
                        person.id,
                        person.name,
                        face.embedding_id,
                        decrypt_embedding(
                            face.embedding_encrypted,
                            self.settings.rtsp_encryption_key.get_secret_value(),
                        ),
                    )
                    for face, person in rows
                ]
                self.key, self.revision = key, state.revision
            return self.key, list(self.references)

    def search(self, vector, *, allowed_person_ids=None, provider=None, limit=10):
        with self.lock:
            key, references = self.snapshot()
            if allowed_person_ids is not None:
                allowed = set(allowed_person_ids)
                references = [ref for ref in references if ref.person_id in allowed]
            chosen = provider or self.settings.face_search_provider
            if chosen == "auto":
                chosen = (
                    "memory"
                    if len(references) <= self.settings.face_memory_max_references
                    else "qdrant"
                )
            matches = (self.memory if chosen == "memory" else self.qdrant).search(
                vector, references, limit
            )
            return {
                "gallery_revision": key[0],
                "provider": chosen,
                "matches": matches,
                "threshold": self.settings.face_match_threshold,
            }

    def reload(self, revision):
        with self.lock:
            self.key = None
            self.snapshot()
            if self.revision < revision:
                raise RuntimeError("Gallery revision not visible")
            return {"revision": self.revision, "references": len(self.references)}

    def close(self):
        with self.lock:
            self.references.clear()
            if self.owned:
                self.client.close()
                self.engine.dispose()
