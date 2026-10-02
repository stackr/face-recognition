"""Versioned cosine search; aggregate the best reference score for each person."""

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from cryptography.fernet import Fernet
from qdrant_client import models as qm

from app.core.face_data import MODEL_VERSION, normalized_embedding


@dataclass(frozen=True)
class Reference:
    face_id: int
    person_id: int
    name: str
    embedding_id: str
    vector: np.ndarray


class FaceSearchProvider(Protocol):
    def search(self, vector, references: list[Reference], limit=10) -> list[dict]: ...


def aggregate(scored, limit):
    best = {}
    for ref, score in scored:
        score = float(np.clip(score, -1, 1))
        old = best.get(ref.person_id)
        if old is None or (-score, ref.face_id) < (-old["similarity"], old["face_id"]):
            best[ref.person_id] = {
                "person_id": ref.person_id,
                "name": ref.name,
                "face_id": ref.face_id,
                "similarity": score,
            }
    return sorted(best.values(), key=lambda item: (-item["similarity"], item["person_id"]))[:limit]


class MemoryFaceSearch:
    def search(self, vector, references, limit=10):
        query = normalized_embedding(vector)
        if not references:
            return []
        scores = np.stack([ref.vector for ref in references]) @ query
        return aggregate(zip(references, scores, strict=True), limit)


class QdrantFaceSearch:
    def __init__(self, client, collection):
        self.client, self.collection = client, collection

    def search(self, vector, references, limit=10):
        query = normalized_embedding(vector)
        if not references:
            return []
        allowed = {ref.embedding_id: ref for ref in references}
        # Exact evaluation of all eligible references keeps max-per-person semantics
        # identical to memory search, including people with many duplicate references.
        response = self.client.query_points(
            collection_name=self.collection,
            query=query.tolist(),
            limit=len(allowed),
            query_filter=qm.Filter(
                must=[
                    qm.HasIdCondition(has_id=list(allowed)),
                    qm.FieldCondition(
                        key="model_version", match=qm.MatchValue(value=MODEL_VERSION)
                    ),
                ]
            ),
            search_params=qm.SearchParams(exact=True),
            with_payload=False,
            with_vectors=False,
        )
        return aggregate(
            [
                (allowed[str(point.id)], point.score)
                for point in response.points
                if str(point.id) in allowed
            ],
            limit,
        )


def ensure_collection(client, name):
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=qm.VectorParams(size=512, distance=qm.Distance.COSINE),
            metadata={"owner": "cctv-search", "model_version": MODEL_VERSION},
        )
    config = client.get_collection(name).config
    vectors = config.params.vectors
    metadata = config.metadata or {}
    if (
        not isinstance(vectors, qm.VectorParams)
        or vectors.size != 512
        or vectors.distance != qm.Distance.COSINE
        or metadata.get("owner") != "cctv-search"
        or metadata.get("model_version") != MODEL_VERSION
    ):
        raise ValueError("Incompatible face collection; migration required")


def encrypt_embedding(vector, key):
    return (
        Fernet(key.encode()).encrypt(normalized_embedding(vector).astype("<f4").tobytes()).decode()
    )


def decrypt_embedding(ciphertext, key):
    raw = Fernet(key.encode()).decrypt(ciphertext.encode())
    return normalized_embedding(np.frombuffer(raw, dtype="<f4"))
