"""Persist private object keys, not access URLs. Back up before applying."""
from alembic import op
import sqlalchemy as sa
revision = "017"
down_revision = "016"
branch_labels = depends_on = None

def upgrade():
    op.add_column("autobiographies", sa.Column("pdf_key", sa.String(2048), nullable=True))
    # PDFs have always used this deterministic key; URL presence marks a completed upload.
    op.execute("UPDATE autobiographies SET pdf_key = 'users/' || user_id::text || '/manuscripts/' || id::text || '.pdf' WHERE pdf_url IS NOT NULL")
    op.drop_column("autobiographies", "pdf_url")
    op.drop_column("media_assets", "s3_url")

def downgrade():
    # Public URLs cannot be reconstructed safely. Consumers must regenerate signed URLs.
    op.add_column("media_assets", sa.Column("s3_url", sa.String(2048), nullable=False, server_default=""))
    op.add_column("autobiographies", sa.Column("pdf_url", sa.String(2048), nullable=True))
    op.drop_column("autobiographies", "pdf_key")
