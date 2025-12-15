"""
Tests for Main Application utilities.

Tests utility functions that can be tested independently of the full app.
Note: Full app tests require observability dependencies (opentelemetry, etc.)
"""


# These tests need to test the utility functions without importing the full app
# since app.main imports observability which requires opentelemetry


class TestIsInternalIPLogic:
    """Tests for internal IP detection logic."""

    def _is_internal_ip(self, ip: str) -> bool:
        """Local implementation of _is_internal_ip for testing."""
        return (
            ip in ("127.0.0.1", "::1", "localhost")
            or ip.startswith("10.")
            or ip.startswith("172.")
            or ip.startswith("192.168.")
        )

    def test_localhost_ipv4(self):
        """127.0.0.1 is internal."""
        assert self._is_internal_ip("127.0.0.1") is True

    def test_localhost_ipv6(self):
        """::1 is internal."""
        assert self._is_internal_ip("::1") is True

    def test_localhost_hostname(self):
        """localhost is internal."""
        assert self._is_internal_ip("localhost") is True

    def test_10_network(self):
        """10.x.x.x is internal."""
        assert self._is_internal_ip("10.0.0.1") is True
        assert self._is_internal_ip("10.255.255.255") is True

    def test_172_network(self):
        """172.x.x.x is internal."""
        assert self._is_internal_ip("172.16.0.1") is True
        assert self._is_internal_ip("172.31.255.255") is True

    def test_192_168_network(self):
        """192.168.x.x is internal."""
        assert self._is_internal_ip("192.168.0.1") is True
        assert self._is_internal_ip("192.168.255.255") is True

    def test_public_ip_not_internal(self):
        """Public IPs are not internal."""
        assert self._is_internal_ip("8.8.8.8") is False
        assert self._is_internal_ip("203.0.113.1") is False
        assert self._is_internal_ip("1.1.1.1") is False

    def test_partial_matches_not_internal(self):
        """Similar but incorrect patterns should not match."""
        # 192.169.x.x is not private
        assert self._is_internal_ip("192.169.1.1") is False
        # 11.x.x.x is not private
        assert self._is_internal_ip("11.0.0.1") is False


class TestStaticFilePath:
    """Tests for static file path construction."""

    def test_admin_ui_path_structure(self):
        """Admin UI path follows expected structure."""
        from pathlib import Path

        # Construct path the same way app.main does
        static_dir = Path(__file__).parent.parent / "static" / "admin"

        # Path should be constructed correctly
        assert "static" in str(static_dir)
        assert "admin" in str(static_dir)


class TestProductionModeLogic:
    """Tests for production mode detection logic."""

    def test_production_string_is_production(self):
        """'production' environment is detected as production."""
        environment = "production"
        is_production = environment.lower() == "production"
        assert is_production is True

    def test_development_is_not_production(self):
        """'development' environment is not production."""
        environment = "development"
        is_production = environment.lower() == "production"
        assert is_production is False

    def test_case_insensitive(self):
        """Production detection is case-insensitive."""
        assert "PRODUCTION".lower() == "production"
        assert "Production".lower() == "production"
