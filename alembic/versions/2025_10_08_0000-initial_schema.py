"""initial schema

Revision ID: 2025_10_08_0000
Revises:
Create Date: 2025-10-08 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = '2025_10_08_0000'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create initial database schema."""

    # ========================================================================
    # Create accounts table
    # ========================================================================
    op.create_table(
        'accounts',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('oauth_provider', sa.String(255), nullable=False),
        sa.Column('external_id', sa.String(255), nullable=False),
        sa.Column('wa_id', sa.String(255), nullable=True),
        sa.Column('tenant_id', sa.String(255), nullable=True),
        sa.Column('balance_minor', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('currency', sa.String(3), nullable=False, server_default='USD'),
        sa.Column('plan_name', sa.String(100), nullable=False, server_default='free'),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),

        # Constraints
        sa.CheckConstraint('balance_minor >= 0', name='ck_balance_non_negative'),
        sa.CheckConstraint("status IN ('active', 'suspended', 'closed')", name='ck_account_status'),
        sa.UniqueConstraint('oauth_provider', 'external_id', 'wa_id', 'tenant_id', name='uq_account_identity'),
    )

    # Indexes for accounts
    op.create_index('idx_accounts_oauth_external', 'accounts', ['oauth_provider', 'external_id'])
    op.create_index('idx_accounts_wa_id', 'accounts', ['wa_id'], postgresql_where=sa.text('wa_id IS NOT NULL'))
    op.create_index('idx_accounts_tenant_id', 'accounts', ['tenant_id'], postgresql_where=sa.text('tenant_id IS NOT NULL'))
    op.create_index('idx_accounts_status', 'accounts', ['status'])
    op.create_index('idx_accounts_updated_at', 'accounts', ['updated_at'])

    # ========================================================================
    # Create charges table
    # ========================================================================
    op.create_table(
        'charges',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('account_id', UUID(as_uuid=True), nullable=False),
        sa.Column('amount_minor', sa.BigInteger(), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('balance_before', sa.BigInteger(), nullable=False),
        sa.Column('balance_after', sa.BigInteger(), nullable=False),
        sa.Column('description', sa.String(), nullable=False),
        sa.Column('idempotency_key', sa.String(255), nullable=True),
        sa.Column('metadata_message_id', sa.String(255), nullable=True),
        sa.Column('metadata_agent_id', sa.String(255), nullable=True),
        sa.Column('metadata_channel_id', sa.String(255), nullable=True),
        sa.Column('metadata_request_id', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),

        # Constraints
        sa.CheckConstraint('amount_minor > 0', name='ck_charge_amount_positive'),
        sa.CheckConstraint('balance_after = balance_before - amount_minor', name='ck_charge_balance_consistency'),
        sa.UniqueConstraint('account_id', 'idempotency_key', name='uq_charge_idempotency'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name='fk_charges_account', ondelete='RESTRICT'),
    )

    # Indexes for charges
    op.create_index('idx_charges_account_id', 'charges', ['account_id'])
    op.create_index('idx_charges_created_at', 'charges', ['created_at'])
    op.create_index('idx_charges_idempotency_key', 'charges', ['idempotency_key'], postgresql_where=sa.text('idempotency_key IS NOT NULL'))
    op.create_index('idx_charges_request_id', 'charges', ['metadata_request_id'], postgresql_where=sa.text('metadata_request_id IS NOT NULL'))

    # ========================================================================
    # Create credits table
    # ========================================================================
    op.create_table(
        'credits',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('account_id', UUID(as_uuid=True), nullable=False),
        sa.Column('amount_minor', sa.BigInteger(), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('balance_before', sa.BigInteger(), nullable=False),
        sa.Column('balance_after', sa.BigInteger(), nullable=False),
        sa.Column('transaction_type', sa.String(20), nullable=False),
        sa.Column('description', sa.String(), nullable=False),
        sa.Column('external_transaction_id', sa.String(255), nullable=True),
        sa.Column('idempotency_key', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),

        # Constraints
        sa.CheckConstraint('amount_minor > 0', name='ck_credit_amount_positive'),
        sa.CheckConstraint('balance_after = balance_before + amount_minor', name='ck_credit_balance_consistency'),
        sa.CheckConstraint("transaction_type IN ('purchase', 'grant', 'refund', 'adjustment')", name='ck_transaction_type'),
        sa.UniqueConstraint('account_id', 'idempotency_key', name='uq_credit_idempotency'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name='fk_credits_account', ondelete='RESTRICT'),
    )

    # Indexes for credits
    op.create_index('idx_credits_account_id', 'credits', ['account_id'])
    op.create_index('idx_credits_created_at', 'credits', ['created_at'])
    op.create_index('idx_credits_transaction_type', 'credits', ['transaction_type'])
    op.create_index('idx_credits_external_transaction_id', 'credits', ['external_transaction_id'], postgresql_where=sa.text('external_transaction_id IS NOT NULL'))

    # ========================================================================
    # Create credit_checks table
    # ========================================================================
    op.create_table(
        'credit_checks',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('account_id', UUID(as_uuid=True), nullable=True),
        sa.Column('oauth_provider', sa.String(255), nullable=False),
        sa.Column('external_id', sa.String(255), nullable=False),
        sa.Column('wa_id', sa.String(255), nullable=True),
        sa.Column('tenant_id', sa.String(255), nullable=True),
        sa.Column('has_credit', sa.Boolean(), nullable=False),
        sa.Column('credits_remaining', sa.BigInteger(), nullable=True),
        sa.Column('plan_name', sa.String(100), nullable=True),
        sa.Column('denial_reason', sa.String(), nullable=True),
        sa.Column('context_agent_id', sa.String(255), nullable=True),
        sa.Column('context_channel_id', sa.String(255), nullable=True),
        sa.Column('context_request_id', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),

        # Foreign key
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name='fk_credit_checks_account', ondelete='SET NULL'),
    )

    # Indexes for credit_checks
    op.create_index('idx_credit_checks_account_id', 'credit_checks', ['account_id'], postgresql_where=sa.text('account_id IS NOT NULL'))
    op.create_index('idx_credit_checks_created_at', 'credit_checks', ['created_at'])
    op.create_index('idx_credit_checks_oauth_external', 'credit_checks', ['oauth_provider', 'external_id'])
    op.create_index('idx_credit_checks_has_credit', 'credit_checks', ['has_credit'])


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table('credit_checks')
    op.drop_table('credits')
    op.drop_table('charges')
    op.drop_table('accounts')
