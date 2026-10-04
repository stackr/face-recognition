"""Remember uploaded video display names without exposing private storage paths."""

import sqlalchemy as sa
from alembic import op

revision = "0008_video_filename"
down_revision = "0007_event_clips"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cameras", sa.Column("video_filename", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("cameras", "video_filename")
