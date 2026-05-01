"""
Smoke tests for the Spock DDL migration helper (Issue #5 fix).

The migration `2026_05_01_0020-spock_replicate_oauth_and_payment_intent_tables`
must pick the right code path based on whether the `spock` extension is
installed. We can't run actual Spock replication in unit tests; instead we
verify the dispatch logic and the SQL strings are well-formed.
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "2026_05_01_0020-spock_replicate_oauth_and_payment_intent_tables.py"
)


@pytest.fixture
def migration_module():
    """Load the migration module by file path (filename has no valid Python identifier prefix)."""
    spec = importlib.util.spec_from_file_location("migration_0020", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_detects_spock_extension_present(migration_module):
    """When pg_extension contains spock, _has_spock_extension returns True."""
    bind = MagicMock()
    bind.execute.return_value.scalar.return_value = 1
    assert migration_module._has_spock_extension(bind) is True


def test_migration_detects_spock_extension_absent(migration_module):
    """When pg_extension does not contain spock, _has_spock_extension returns False."""
    bind = MagicMock()
    bind.execute.return_value.scalar.return_value = None
    assert migration_module._has_spock_extension(bind) is False


def test_ensure_ddl_is_idempotent(migration_module):
    """The DDL must use IF NOT EXISTS so re-running on a peer that already has the tables is a no-op."""
    ddl = migration_module._ENSURE_DDL
    assert "CREATE TABLE IF NOT EXISTS admin_oauth_sessions" in ddl
    assert "CREATE TABLE IF NOT EXISTS stripe_payment_intents" in ddl
    assert "CREATE INDEX IF NOT EXISTS idx_admin_oauth_sessions_expires_at" in ddl
    assert "CREATE INDEX IF NOT EXISTS idx_stripe_payment_intents_account_id" in ddl
    assert "CREATE INDEX IF NOT EXISTS idx_stripe_payment_intents_status" in ddl


def test_upgrade_uses_replicate_ddl_when_spock_present(monkeypatch, migration_module):
    """Upgrade dispatches via spock.replicate_ddl when the extension is installed."""
    bind = MagicMock()
    bind.execute.return_value.scalar.return_value = 1  # spock present

    captured_sql = []

    def capture_execute(stmt, *args, **kwargs):
        captured_sql.append(str(stmt))
        result = MagicMock()
        result.scalar.return_value = 1
        return result

    bind.execute.side_effect = capture_execute

    monkeypatch.setattr(migration_module.op, "get_bind", lambda: bind, raising=False)

    migration_module.upgrade()

    # Two execute() calls: one to detect, one to dispatch via replicate_ddl.
    assert any(
        "spock.replicate_ddl" in s for s in captured_sql
    ), f"Expected spock.replicate_ddl in dispatched SQL; got: {captured_sql}"


def test_upgrade_uses_plain_ddl_when_spock_absent(monkeypatch, migration_module):
    """Upgrade falls back to plain DDL when spock is absent (CI, local dev)."""
    bind = MagicMock()

    captured_sql = []
    call_count = [0]

    def capture_execute(stmt, *args, **kwargs):
        captured_sql.append(str(stmt))
        result = MagicMock()
        # First call is the spock-extension detection — return None.
        if call_count[0] == 0:
            result.scalar.return_value = None
        else:
            result.scalar.return_value = None
        call_count[0] += 1
        return result

    bind.execute.side_effect = capture_execute

    monkeypatch.setattr(migration_module.op, "get_bind", lambda: bind, raising=False)

    migration_module.upgrade()

    # No spock.replicate_ddl call; instead the DDL itself is executed directly.
    assert not any(
        "spock.replicate_ddl" in s for s in captured_sql
    ), f"Should not call spock.replicate_ddl when extension is absent; got: {captured_sql}"
    # The CREATE TABLE statements are passed directly via sa.text(_ENSURE_DDL).
    # sa.text() repr won't include the body; we verified _ENSURE_DDL contents above.
    assert call_count[0] == 2  # detection + DDL


def test_revision_chain(migration_module):
    """Migration revision identifiers must form the expected chain."""
    assert migration_module.revision == "2026_05_01_0020"
    assert migration_module.down_revision == "2026_05_01_0019"
