"""Private, owner-scoped video test storage. No embeddings are written to disk."""

import json
import os
import uuid
from pathlib import Path


def identifier(value):
    parsed = uuid.UUID(str(value))
    if str(parsed) != str(value):
        raise ValueError("Invalid job identifier")
    return str(parsed)


def private_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("Invalid private directory")
    path.chmod(0o700)
    return path


def write_private(path, content):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4()}.tmp")
    try:
        with os.fdopen(
            os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
        ) as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_metadata(path, value):
    write_private(path, json.dumps(value, ensure_ascii=False, allow_nan=False).encode())


def storage_size(root):
    return sum(
        path.stat().st_size
        for path in Path(root).rglob("*")
        if path.is_file() and not path.is_symlink()
    )
