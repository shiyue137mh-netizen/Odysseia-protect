"""add trace_enabled to resources

Revision ID: 8c2f1a4d7b90
Revises: 5e6f70913e2c
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8c2f1a4d7b90"
down_revision: Union[str, Sequence[str], None] = "5e6f70913e2c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "resources",
        sa.Column(
            "trace_enabled",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("resources", "trace_enabled")
