"""Preserve registered RTSP cameras; add optional private MP4 sources."""

import sqlalchemy as sa
from alembic import op

revision = "0002_video_sources"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "cameras", sa.Column("source_type", sa.String(8), nullable=False, server_default="rtsp")
    )
    op.add_column("cameras", sa.Column("video_path", sa.String(255), nullable=True))
    op.alter_column("cameras", "source_type", existing_type=sa.String(8), server_default=None)


def downgrade():
    op.drop_column("cameras", "video_path")
    op.drop_column("cameras", "source_type")
