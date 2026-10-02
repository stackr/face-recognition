"""Reference persons, permissions and durable face lifecycle jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0004_person_faces"
down_revision = "0003_camera_permissions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "persons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("deleting", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "person_faces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "person_id",
            sa.Integer(),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("image_path", sa.String(64)),
        sa.Column("embedding_id", sa.String(36), unique=True, nullable=False),
        sa.Column("embedding_encrypted", sa.Text()),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("quality", sa.Float(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("image_expires_at", sa.DateTime(), nullable=False),
        sa.Column("embedding_expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for field in ("person_id", "image_expires_at", "embedding_expires_at"):
        op.create_index("ix_person_faces_" + field, "person_faces", [field])
    op.create_table(
        "person_permissions",
        sa.Column(
            "person_id",
            sa.Integer(),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
    )
    state = op.create_table(
        "face_gallery_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.bulk_insert(state, [{"id": 1, "revision": 1}])
    op.create_table(
        "face_cleanup_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_key", sa.String(100), unique=True, nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("face_id", sa.Integer()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(80)),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
    )
    for field in ("person_id", "status", "next_attempt_at"):
        op.create_index("ix_face_cleanup_jobs_" + field, "face_cleanup_jobs", [field])


def downgrade():
    for name in (
        "face_cleanup_jobs",
        "face_gallery_state",
        "person_permissions",
        "person_faces",
        "persons",
    ):
        op.drop_table(name)
