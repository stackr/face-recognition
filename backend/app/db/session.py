from collections.abc import Generator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings


def make_engine(settings: Settings):
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=5,
        hide_parameters=True,
        connect_args={"connect_timeout": 3, "read_timeout": 5, "write_timeout": 5},
    )


def get_db(request: Request) -> Generator[Session, None, None]:
    with Session(request.app.state.engine) as session:
        yield session
