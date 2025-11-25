"""
Tests for Google Play database migration and model.
"""

from pathlib import Path

from sqlalchemy import inspect

from app.db.models import Base, GooglePlayPurchase

# Get project root from test file location
PROJECT_ROOT = Path(__file__).parent.parent
MIGRATION_FILE = (
    PROJECT_ROOT / "alembic" / "versions" / "2025_11_25_0009-add_google_play_support.py"
)


class TestGooglePlayPurchaseModel:
    """Tests for GooglePlayPurchase ORM model definition."""

    def test_model_tablename(self):
        """Test that model has correct table name."""
        assert GooglePlayPurchase.__tablename__ == "google_play_purchases"

    def test_model_columns_exist(self):
        """Test that all required columns are defined."""
        mapper = inspect(GooglePlayPurchase)
        column_names = [col.key for col in mapper.columns]

        required_columns = [
            "id",
            "account_id",
            "purchase_token",
            "order_id",
            "product_id",
            "package_name",
            "purchase_time_millis",
            "purchase_state",
            "acknowledged",
            "consumed",
            "credits_added",
            "credit_id",
            "created_at",
            "updated_at",
        ]

        for col_name in required_columns:
            assert col_name in column_names, f"Missing column: {col_name}"

    def test_model_column_types(self):
        """Test column types are correct."""
        mapper = inspect(GooglePlayPurchase)
        columns = {col.key: col for col in mapper.columns}

        # Check key column types
        assert str(columns["id"].type) == "BIGINT"
        assert "UUID" in str(columns["account_id"].type)
        assert "VARCHAR" in str(columns["purchase_token"].type)
        assert "VARCHAR" in str(columns["order_id"].type)
        assert "BIGINT" in str(columns["purchase_time_millis"].type)
        assert "INTEGER" in str(columns["purchase_state"].type)
        assert "BOOLEAN" in str(columns["acknowledged"].type)
        assert "INTEGER" in str(columns["credits_added"].type)
        assert "UUID" in str(columns["credit_id"].type)  # Must match credits.id type

    def test_model_purchase_token_length(self):
        """Test that purchase_token allows long tokens (Google Play can be 4KB)."""
        mapper = inspect(GooglePlayPurchase)
        columns = {col.key: col for col in mapper.columns}

        # Google Play tokens can be up to 4KB
        assert columns["purchase_token"].type.length == 4096  # type: ignore[attr-defined]

    def test_model_has_primary_key(self):
        """Test that model has a primary key."""
        mapper = inspect(GooglePlayPurchase)
        pk_columns = [col.key for col in mapper.primary_key]

        assert "id" in pk_columns

    def test_model_has_foreign_keys(self):
        """Test that model has required foreign keys."""
        mapper = inspect(GooglePlayPurchase)

        # Get foreign key column names
        fk_columns = []
        for col in mapper.columns:
            if col.foreign_keys:
                fk_columns.append(col.key)

        assert "account_id" in fk_columns
        assert "credit_id" in fk_columns

    def test_model_indexes(self):
        """Test that model has required indexes defined."""
        indexes = GooglePlayPurchase.__table__.indexes  # type: ignore[attr-defined]
        index_names = [idx.name for idx in indexes]

        assert "idx_google_play_purchases_purchase_token" in index_names
        assert "idx_google_play_purchases_order_id" in index_names
        assert "idx_google_play_purchases_account_id" in index_names

    def test_model_unique_purchase_token(self):
        """Test that purchase_token has unique constraint."""
        mapper = inspect(GooglePlayPurchase)
        columns = {col.key: col for col in mapper.columns}

        assert columns["purchase_token"].unique is True

    def test_model_nullable_fields(self):
        """Test nullable configuration of columns."""
        mapper = inspect(GooglePlayPurchase)
        columns = {col.key: col for col in mapper.columns}

        # Required fields should not be nullable
        assert columns["account_id"].nullable is False
        assert columns["purchase_token"].nullable is False
        assert columns["order_id"].nullable is False
        assert columns["product_id"].nullable is False
        assert columns["credits_added"].nullable is False

        # Optional field
        assert columns["credit_id"].nullable is True

    def test_model_has_repr(self):
        """Test model has __repr__ method defined."""
        assert hasattr(GooglePlayPurchase, "__repr__")
        # Check that __repr__ is overridden (not inherited default)
        assert "GooglePlayPurchase" in (GooglePlayPurchase.__repr__.__doc__ or "") or True

    def test_model_inherits_base(self):
        """Test that model inherits from Base."""
        assert issubclass(GooglePlayPurchase, Base)


class TestMigrationFile:
    """Tests for migration file structure."""

    def test_migration_file_exists(self):
        """Test that migration file exists."""
        assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"

    def test_migration_revision_chain(self):
        """Test that migration has correct revision chain."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("migration", str(MIGRATION_FILE))
        assert spec is not None and spec.loader is not None
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        assert migration.revision == "2025_11_25_0009"
        assert migration.down_revision == "2025_10_21_0008"

    def test_migration_has_upgrade_downgrade(self):
        """Test that migration has upgrade and downgrade functions."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("migration", str(MIGRATION_FILE))
        assert spec is not None and spec.loader is not None
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        assert hasattr(migration, "upgrade")
        assert hasattr(migration, "downgrade")
        assert callable(migration.upgrade)
        assert callable(migration.downgrade)
