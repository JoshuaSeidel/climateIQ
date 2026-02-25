"""Add embedding column to user_directives for semantic memory search.

Adds a vector(1536) column to user_directives so memories extracted from
chat conversations can be embedded and retrieved via pgvector similarity
search. The ivfflat index enables fast approximate nearest-neighbour queries
against the growing memory store.

Revision ID: 003_memory_embeddings
Revises: 002_perf_indexes
Create Date: 2026-02-25
"""

from alembic import op

revision = "003_memory_embeddings"
down_revision = "002_perf_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure pgvector extension is available (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("ALTER TABLE user_directives ADD COLUMN IF NOT EXISTS embedding vector(1536)")

    # ivfflat index for approximate nearest-neighbour search (cosine distance)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_directives_embedding "
        "ON user_directives USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_user_directives_embedding")
    op.execute("ALTER TABLE user_directives DROP COLUMN IF EXISTS embedding")
