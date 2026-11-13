"""create urls table

Revision ID: 382659b0c89f
Revises:
Create Date: 2026-10-31 01:51:20.188466

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '382659b0c89f'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Autogenerate does not create standalone sequences; create this first.
    op.execute(sa.schema.CreateSequence(
        sa.Sequence('url_id_seq', start=1, data_type=sa.BigInteger())
    ))
    op.create_table('urls',
    sa.Column('id', sa.BigInteger(), server_default=sa.text("nextval('url_id_seq')"), nullable=False),
    sa.Column('original_url', sa.Text(), nullable=False),
    sa.Column('short_code', sa.String(length=11), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('short_code')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('urls')
    op.execute(sa.schema.DropSequence(sa.Sequence('url_id_seq')))
