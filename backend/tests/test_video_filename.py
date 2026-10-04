import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_video_filename_migration_preserves_existing_upload(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'filename.sqlite'}")
    path = Path(__file__).parents[1] / "migrations/versions/0008_video_filename.py"
    spec = importlib.util.spec_from_file_location("filename_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE cameras (id INTEGER PRIMARY KEY, video_path VARCHAR(255))")
        )
        connection.execute(text("INSERT INTO cameras VALUES (1, 'private.mp4')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(
                text("SELECT video_path, video_filename FROM cameras")
            ).one() == ("private.mp4", None)
            connection.execute(text("UPDATE cameras SET video_filename = 'sample.mp4'"))
            migration.downgrade()
        assert connection.execute(text("SELECT video_path FROM cameras")).scalar() == "private.mp4"
        assert "video_filename" not in {
            column["name"] for column in inspect(connection).get_columns("cameras")
        }
