"""update admin users for oauth

Revision ID: 2025_10_08_0003
Revises: 2025_10_08_0002
Create Date: 2025-10-08 12:00:00.000000

Updates admin_users table for Google OAuth authentication:
- Remove password-based auth fields (password_hash, mfa_enabled, mfa_secret)
- Add OAuth fields (google_id, picture_url)
- Simplify roles to 2 (admin, viewer)
- Add domain constraint (@ciris.ai only)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '2025_10_08_0003'
down_revision: Union[str, None] = '2025_10_08_0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new OAuth columns
    op.add_column('admin_users', sa.Column('google_id', sa.String(length=255), nullable=True))
    op.add_column('admin_users', sa.Column('picture_url', sa.String(length=512), nullable=True))

    # Create unique constraint on google_id
    op.create_unique_constraint('uq_admin_users_google_id', 'admin_users', ['google_id'])

    # Create index on google_id
    op.create_index('idx_admin_users_google_id', 'admin_users', ['google_id'])

    # Drop old password-based auth columns
    op.drop_column('admin_users', 'password_hash')
    op.drop_column('admin_users', 'mfa_enabled')
    op.drop_column('admin_users', 'mfa_secret')

    # Update role constraint - remove super_admin
    op.drop_constraint('ck_admin_users_role', 'admin_users', type_='check')
    op.create_check_constraint(
        'ck_admin_users_role',
        'admin_users',
        "role IN ('admin', 'viewer')"
    )

    # Add domain constraint - @ciris.ai only
    op.create_check_constraint(
        'ck_admin_users_ciris_domain',
        'admin_users',
        "email LIKE '%@ciris.ai'"
    )

    # Update email constraint to allow @ciris.ai validation
    op.drop_constraint('ck_admin_users_email_format', 'admin_users', type_='check')


def downgrade() -> None:
    # Re-add password-based auth columns
    op.add_column('admin_users', sa.Column('password_hash', sa.Text(), nullable=False, server_default=''))
    op.add_column('admin_users', sa.Column('mfa_enabled', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('admin_users', sa.Column('mfa_secret', sa.Text(), nullable=True))

    # Drop OAuth columns
    op.drop_index('idx_admin_users_google_id', 'admin_users')
    op.drop_constraint('uq_admin_users_google_id', 'admin_users', type_='unique')
    op.drop_column('admin_users', 'google_id')
    op.drop_column('admin_users', 'picture_url')

    # Restore old role constraint with super_admin
    op.drop_constraint('ck_admin_users_role', 'admin_users', type_='check')
    op.create_check_constraint(
        'ck_admin_users_role',
        'admin_users',
        "role IN ('super_admin', 'admin', 'viewer')"
    )

    # Drop domain constraint
    op.drop_constraint('ck_admin_users_ciris_domain', 'admin_users', type_='check')

    # Restore email format constraint
    op.create_check_constraint(
        'ck_admin_users_email_format',
        'admin_users',
        "email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}$'"
    )
