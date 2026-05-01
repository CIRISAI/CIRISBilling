"""Add stripe_payment_intents table for authoritative account binding.

Revision ID: 2026_05_01_0019
Revises: 2026_05_01_0018
Create Date: 2026-05-01

Closes AV-3 (Stripe metadata trust): the webhook handler used to
reconstruct the credited account from PaymentIntent metadata, which
the merchant-side caller controls. We now persist the authoritative
account binding at PaymentIntent creation time, and the webhook
handler resolves the account from this table — failing closed if no
row exists.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_05_01_0019"
down_revision: str | None = "2026_05_01_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create stripe_payment_intents table."""
    op.create_table(
        "stripe_payment_intents",
        sa.Column("payment_intent_id", sa.String(255), primary_key=True),
        sa.Column(
            "account_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("oauth_provider", sa.String(255), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("uses_purchased", sa.Integer, nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="created"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("credited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_stripe_payment_intents_account_id",
        "stripe_payment_intents",
        ["account_id"],
    )
    op.create_index(
        "idx_stripe_payment_intents_status",
        "stripe_payment_intents",
        ["status"],
    )


def downgrade() -> None:
    """Drop stripe_payment_intents table."""
    op.drop_index(
        "idx_stripe_payment_intents_status",
        table_name="stripe_payment_intents",
    )
    op.drop_index(
        "idx_stripe_payment_intents_account_id",
        table_name="stripe_payment_intents",
    )
    op.drop_table("stripe_payment_intents")
