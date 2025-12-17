"""
Migration Runner - Runs Alembic migrations at application startup.

Follows the CIRISAgent pattern of automatically applying pending migrations
when the application starts.
"""

import logging
import os
from pathlib import Path

from sqlalchemy import create_engine

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

logger = logging.getLogger(__name__)

# Path to alembic.ini relative to project root
ALEMBIC_INI_PATH = Path(__file__).parent.parent.parent / "alembic.ini"


def _get_sync_database_url() -> str:
    """Get synchronous database URL for migrations.

    Alembic's command API uses synchronous connections, so we need to
    convert asyncpg URLs to psycopg2 URLs.
    """
    url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://billing_admin:password@localhost:5432/ciris_billing",
    )
    # Convert async URL to sync for alembic command API
    return url.replace("asyncpg", "psycopg2")


def _get_current_revision(engine) -> str | None:
    """Get the current database revision."""
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        return context.get_current_revision()


def _get_head_revision(alembic_cfg: Config) -> str:
    """Get the head revision from migration scripts."""
    script = ScriptDirectory.from_config(alembic_cfg)
    return script.get_current_head()


def run_migrations() -> None:
    """
    Run pending Alembic migrations.

    Called at application startup to ensure the database schema is up to date.
    Only runs migrations if there are pending ones.
    """
    if not ALEMBIC_INI_PATH.exists():
        logger.warning(f"Alembic config not found at {ALEMBIC_INI_PATH}, skipping migrations")
        return

    try:
        # Create Alembic config
        alembic_cfg = Config(str(ALEMBIC_INI_PATH))

        # Override the database URL from environment
        sync_url = _get_sync_database_url()
        alembic_cfg.set_main_option("sqlalchemy.url", sync_url.replace("%", "%%"))

        # Create sync engine to check current state
        engine = create_engine(sync_url)

        try:
            current = _get_current_revision(engine)
            head = _get_head_revision(alembic_cfg)

            if current == head:
                logger.info(f"Database schema is up to date (revision: {current})")
                return

            logger.info(f"Running migrations from {current} to {head}")

            # Run the upgrade
            command.upgrade(alembic_cfg, "head")

            # Verify
            new_current = _get_current_revision(engine)
            logger.info(f"Migrations complete. Database now at revision: {new_current}")

        finally:
            engine.dispose()

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise RuntimeError(f"Database migration failed: {e}") from e


def check_migrations_status() -> dict:
    """
    Check migration status without applying them.

    Returns:
        Dict with current revision, head revision, and whether migrations are pending.
    """
    if not ALEMBIC_INI_PATH.exists():
        return {"error": "Alembic config not found"}

    try:
        alembic_cfg = Config(str(ALEMBIC_INI_PATH))
        sync_url = _get_sync_database_url()
        alembic_cfg.set_main_option("sqlalchemy.url", sync_url.replace("%", "%%"))

        engine = create_engine(sync_url)
        try:
            current = _get_current_revision(engine)
            head = _get_head_revision(alembic_cfg)
            return {
                "current_revision": current,
                "head_revision": head,
                "pending": current != head,
            }
        finally:
            engine.dispose()

    except Exception as e:
        return {"error": str(e)}
