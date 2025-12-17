"""Fix account unique constraint to prevent duplicate accounts.

The existing uq_account_identity constraint includes wa_id and tenant_id,
but NULL values in SQL aren't considered equal, so accounts with NULL
wa_id and tenant_id can still be duplicated.

This migration adds a new unique constraint on just (oauth_provider, external_id)
which are the fields that truly identify a user.

Revision ID: 2025_12_17_0016
Revises: 2025_12_14_0015
Create Date: 2025-12-17

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2025_12_17_0016"
down_revision: str | None = "2025_12_14_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add unique constraint on (oauth_provider, external_id) only."""
    # Add the new constraint that prevents duplicates regardless of wa_id/tenant_id
    op.create_unique_constraint(
        "uq_accounts_oauth_external_id",
        "accounts",
        ["oauth_provider", "external_id"],
    )


def downgrade() -> None:
    """Remove the unique constraint."""
    op.drop_constraint("uq_accounts_oauth_external_id", "accounts", type_="unique")
