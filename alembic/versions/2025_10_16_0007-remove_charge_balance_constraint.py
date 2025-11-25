"""remove charge balance consistency constraint

Revision ID: 2025_10_16_0007
Revises: 2025_10_15_0006
Create Date: 2025-10-16 00:00:00.000000

Removes the ck_charge_balance_consistency constraint that doesn't account
for free uses. When using free tier, balance doesn't change even though
a charge is created.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2025_10_16_0007"
down_revision: str | None = "2025_10_15_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the balance consistency check constraint
    op.drop_constraint("ck_charge_balance_consistency", "charges", type_="check")


def downgrade() -> None:
    # Recreate the constraint
    op.create_check_constraint(
        "ck_charge_balance_consistency", "charges", "balance_after = balance_before - amount_minor"
    )
