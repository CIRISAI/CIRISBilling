"""
Hypothesis Property-Based Tests for CIRISBilling Models.

Uses Hypothesis to generate random valid/invalid inputs and verify:
- Domain model invariants (dataclass validation)
- API model validation (Pydantic validators)
- Serialization round-trips
- Edge cases and boundary conditions
"""

from datetime import UTC, datetime

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from app.models.api import (
    AccountStatus,
    AddCreditsRequest,
    ChargeMetadata,
    CreateAccountRequest,
    CreateChargeRequest,
    CreditCheckRequest,
    CreditCheckResponse,
    GooglePlayVerifyRequest,
    LiteLLMAuthRequest,
    LiteLLMChargeRequest,
    LiteLLMUsageLogRequest,
    TransactionType,
)
from app.models.domain import (
    AccountIdentity,
    BalanceSnapshot,
    ChargeIntent,
    CreditIntent,
    OAuthUser,
)

# ============================================================================
# Hypothesis Strategies - Reusable data generators
# ============================================================================


# OAuth providers must start with "oauth:"
oauth_providers = st.sampled_from(
    [
        "oauth:google",
        "oauth:discord",
        "oauth:github",
        "oauth:apple",
    ]
)

# Invalid OAuth providers (for negative tests)
invalid_oauth_providers = st.text(min_size=1, max_size=50).filter(
    lambda x: not x.startswith("oauth:")
)

# Valid external IDs (non-empty strings)
external_ids = st.text(min_size=1, max_size=255).filter(lambda x: x.strip())

# Valid email addresses
emails = st.emails()

# CIRIS.ai domain emails (for OAuthUser)
ciris_emails = st.builds(
    lambda name: f"{name}@ciris.ai",
    st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789._-"),
        min_size=1,
        max_size=50,
    ).filter(lambda x: x and x[0].isalnum()),
)

# Non-ciris.ai emails (for negative tests)
non_ciris_emails = emails.filter(lambda e: not e.endswith("@ciris.ai"))

# ISO 4217 currency codes (3 uppercase letters)
currencies = st.sampled_from(["USD", "EUR", "GBP", "JPY", "CAD", "AUD"])

# Invalid currencies
invalid_currencies = st.text(min_size=1, max_size=10).filter(lambda x: len(x) != 3)

# Positive amounts (for charges/credits)
positive_amounts = st.integers(min_value=1, max_value=10_000_000)

# Non-positive amounts (for negative tests)
non_positive_amounts = st.integers(max_value=0)

# Non-negative amounts (for balances)
non_negative_amounts = st.integers(min_value=0, max_value=10_000_000)

# Descriptions (non-empty strings)
descriptions = st.text(min_size=1, max_size=500).filter(lambda x: x.strip())

# Optional string IDs
optional_ids = st.one_of(st.none(), st.text(min_size=1, max_size=255))

# Idempotency keys
idempotency_keys = st.one_of(
    st.none(),
    st.text(min_size=1, max_size=255).filter(lambda x: x.strip()),
)

# Timestamps
timestamps = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
    timezones=st.just(UTC),
)

# Transaction types
transaction_types = st.sampled_from(list(TransactionType))

# Account statuses
account_statuses = st.sampled_from(list(AccountStatus))


# ============================================================================
# Composite Strategies - Complex object builders
# ============================================================================


@st.composite
def account_identities(draw):
    """Generate valid AccountIdentity objects."""
    return AccountIdentity(
        oauth_provider=draw(oauth_providers),
        external_id=draw(external_ids),
        wa_id=draw(optional_ids),
        tenant_id=draw(optional_ids),
    )


@st.composite
def charge_metadata(draw):
    """Generate ChargeMetadata objects."""
    return ChargeMetadata(
        message_id=draw(optional_ids),
        agent_id=draw(optional_ids),
        channel_id=draw(optional_ids),
        request_id=draw(optional_ids),
    )


@st.composite
def balance_snapshots(draw):
    """Generate valid BalanceSnapshot objects."""
    return BalanceSnapshot(
        balance_minor=draw(non_negative_amounts),
        currency=draw(currencies),
        timestamp=draw(timestamps),
    )


@st.composite
def charge_intents(draw):
    """Generate valid ChargeIntent objects."""
    return ChargeIntent(
        account_identity=draw(account_identities()),
        amount_minor=draw(positive_amounts),
        currency=draw(currencies),
        description=draw(descriptions),
        metadata=draw(charge_metadata()),
        idempotency_key=draw(idempotency_keys),
    )


@st.composite
def credit_intents(draw):
    """Generate valid CreditIntent objects."""
    return CreditIntent(
        account_identity=draw(account_identities()),
        amount_minor=draw(positive_amounts),
        currency=draw(currencies),
        description=draw(descriptions),
        transaction_type=draw(transaction_types),
        external_transaction_id=draw(optional_ids),
        idempotency_key=draw(idempotency_keys),
        is_test=draw(st.booleans()),
    )


# ============================================================================
# Domain Model Tests - AccountIdentity
# ============================================================================


class TestAccountIdentityProperties:
    """Property-based tests for AccountIdentity domain model."""

    @given(oauth_providers, external_ids, optional_ids, optional_ids)
    @settings(max_examples=100)
    def test_valid_identity_creation(self, oauth_provider, external_id, wa_id, tenant_id):
        """Valid inputs always produce valid AccountIdentity."""
        identity = AccountIdentity(
            oauth_provider=oauth_provider,
            external_id=external_id,
            wa_id=wa_id,
            tenant_id=tenant_id,
        )
        assert identity.oauth_provider == oauth_provider
        assert identity.external_id == external_id
        assert identity.oauth_provider.startswith("oauth:")

    @given(invalid_oauth_providers, external_ids)
    @settings(max_examples=50)
    def test_invalid_oauth_provider_raises(self, oauth_provider, external_id):
        """Invalid oauth_provider always raises ValueError."""
        with pytest.raises(ValueError, match="Invalid oauth_provider"):
            AccountIdentity(
                oauth_provider=oauth_provider,
                external_id=external_id,
                wa_id=None,
                tenant_id=None,
            )

    @given(oauth_providers)
    @settings(max_examples=50)
    def test_empty_external_id_raises(self, oauth_provider):
        """Empty external_id always raises ValueError."""
        with pytest.raises(ValueError, match="external_id cannot be empty"):
            AccountIdentity(
                oauth_provider=oauth_provider,
                external_id="",
                wa_id=None,
                tenant_id=None,
            )

    @given(account_identities())
    @settings(max_examples=50)
    def test_identity_is_immutable(self, identity):
        """AccountIdentity is frozen (immutable)."""
        with pytest.raises(AttributeError):
            identity.external_id = "new_value"


# ============================================================================
# Domain Model Tests - BalanceSnapshot
# ============================================================================


class TestBalanceSnapshotProperties:
    """Property-based tests for BalanceSnapshot domain model."""

    @given(non_negative_amounts, currencies, timestamps)
    @settings(max_examples=100)
    def test_valid_balance_creation(self, balance, currency, timestamp):
        """Valid inputs always produce valid BalanceSnapshot."""
        snapshot = BalanceSnapshot(
            balance_minor=balance,
            currency=currency,
            timestamp=timestamp,
        )
        assert snapshot.balance_minor == balance
        assert snapshot.currency == currency
        assert snapshot.balance_minor >= 0

    @given(st.integers(max_value=-1), currencies, timestamps)
    @settings(max_examples=50)
    def test_negative_balance_raises(self, balance, currency, timestamp):
        """Negative balance always raises ValueError."""
        with pytest.raises(ValueError, match="Balance cannot be negative"):
            BalanceSnapshot(
                balance_minor=balance,
                currency=currency,
                timestamp=timestamp,
            )

    @given(non_negative_amounts, invalid_currencies, timestamps)
    @settings(max_examples=50)
    def test_invalid_currency_raises(self, balance, currency, timestamp):
        """Invalid currency code always raises ValueError."""
        with pytest.raises(ValueError, match="Invalid currency code"):
            BalanceSnapshot(
                balance_minor=balance,
                currency=currency,
                timestamp=timestamp,
            )


# ============================================================================
# Domain Model Tests - ChargeIntent
# ============================================================================


class TestChargeIntentProperties:
    """Property-based tests for ChargeIntent domain model."""

    @given(charge_intents())
    @settings(max_examples=100)
    def test_valid_charge_intent_creation(self, intent):
        """Valid inputs always produce valid ChargeIntent."""
        assert intent.amount_minor > 0
        assert len(intent.currency) == 3
        assert intent.description

    @given(account_identities(), non_positive_amounts, currencies, descriptions)
    @settings(max_examples=50)
    def test_non_positive_amount_raises(self, identity, amount, currency, description):
        """Non-positive charge amount always raises ValueError."""
        with pytest.raises(ValueError, match="Charge amount must be positive"):
            ChargeIntent(
                account_identity=identity,
                amount_minor=amount,
                currency=currency,
                description=description,
                metadata=ChargeMetadata(),
                idempotency_key=None,
            )

    @given(account_identities(), positive_amounts, currencies)
    @settings(max_examples=50)
    def test_empty_description_raises(self, identity, amount, currency):
        """Empty description always raises ValueError."""
        with pytest.raises(ValueError, match="Description cannot be empty"):
            ChargeIntent(
                account_identity=identity,
                amount_minor=amount,
                currency=currency,
                description="",
                metadata=ChargeMetadata(),
                idempotency_key=None,
            )

    @given(charge_intents())
    @settings(max_examples=50)
    def test_charge_intent_is_immutable(self, intent):
        """ChargeIntent is frozen (immutable)."""
        with pytest.raises(AttributeError):
            intent.amount_minor = 999


# ============================================================================
# Domain Model Tests - CreditIntent
# ============================================================================


class TestCreditIntentProperties:
    """Property-based tests for CreditIntent domain model."""

    @given(credit_intents())
    @settings(max_examples=100)
    def test_valid_credit_intent_creation(self, intent):
        """Valid inputs always produce valid CreditIntent."""
        assert intent.amount_minor > 0
        assert len(intent.currency) == 3
        assert intent.description
        assert isinstance(intent.is_test, bool)

    @given(account_identities(), non_positive_amounts, currencies, descriptions, transaction_types)
    @settings(max_examples=50)
    def test_non_positive_amount_raises(self, identity, amount, currency, description, txn_type):
        """Non-positive credit amount always raises ValueError."""
        with pytest.raises(ValueError, match="Credit amount must be positive"):
            CreditIntent(
                account_identity=identity,
                amount_minor=amount,
                currency=currency,
                description=description,
                transaction_type=txn_type,
                external_transaction_id=None,
                idempotency_key=None,
            )

    @given(credit_intents())
    @settings(max_examples=50)
    def test_credit_intent_is_immutable(self, intent):
        """CreditIntent is frozen (immutable)."""
        with pytest.raises(AttributeError):
            intent.is_test = True


# ============================================================================
# Domain Model Tests - OAuthUser
# ============================================================================


class TestOAuthUserProperties:
    """Property-based tests for OAuthUser domain model."""

    @given(st.text(min_size=1, max_size=50), ciris_emails, optional_ids, optional_ids)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.filter_too_much])
    def test_valid_ciris_user_creation(self, user_id, email, name, picture):
        """Valid @ciris.ai emails always produce valid OAuthUser."""
        user = OAuthUser(id=user_id, email=email, name=name, picture=picture)
        assert user.email.endswith("@ciris.ai")

    @given(st.text(min_size=1, max_size=50), non_ciris_emails)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.filter_too_much])
    def test_non_ciris_email_raises(self, user_id, email):
        """Non @ciris.ai emails always raise ValueError."""
        with pytest.raises(ValueError, match="Only @ciris.ai emails allowed"):
            OAuthUser(id=user_id, email=email)


# ============================================================================
# API Model Tests - CreditCheckRequest
# ============================================================================


class TestCreditCheckRequestProperties:
    """Property-based tests for CreditCheckRequest API model."""

    @given(oauth_providers, external_ids, optional_ids, optional_ids)
    @settings(max_examples=100)
    def test_valid_request_creation(self, oauth_provider, external_id, wa_id, tenant_id):
        """Valid inputs always produce valid CreditCheckRequest."""
        request = CreditCheckRequest(
            oauth_provider=oauth_provider,
            external_id=external_id,
            wa_id=wa_id,
            tenant_id=tenant_id,
        )
        assert request.oauth_provider.startswith("oauth:")

    @given(invalid_oauth_providers, external_ids)
    @settings(max_examples=50)
    def test_invalid_oauth_provider_raises(self, oauth_provider, external_id):
        """Invalid oauth_provider always raises ValidationError."""
        assume(oauth_provider)  # Filter empty strings
        with pytest.raises(ValidationError):
            CreditCheckRequest(
                oauth_provider=oauth_provider,
                external_id=external_id,
            )

    @given(oauth_providers, external_ids)
    @settings(max_examples=50)
    def test_response_serialization_roundtrip(self, oauth_provider, external_id):
        """Request can be serialized and deserialized."""
        request = CreditCheckRequest(
            oauth_provider=oauth_provider,
            external_id=external_id,
        )
        data = request.model_dump()
        restored = CreditCheckRequest(**data)
        assert restored.oauth_provider == request.oauth_provider
        assert restored.external_id == request.external_id


# ============================================================================
# API Model Tests - CreateChargeRequest
# ============================================================================


class TestCreateChargeRequestProperties:
    """Property-based tests for CreateChargeRequest API model."""

    @given(oauth_providers, external_ids, positive_amounts, currencies, descriptions)
    @settings(max_examples=100)
    def test_valid_request_creation(
        self, oauth_provider, external_id, amount, currency, description
    ):
        """Valid inputs always produce valid CreateChargeRequest."""
        request = CreateChargeRequest(
            oauth_provider=oauth_provider,
            external_id=external_id,
            amount_minor=amount,
            currency=currency,
            description=description,
        )
        assert request.amount_minor > 0
        assert request.currency == currency.upper()

    @given(oauth_providers, external_ids, non_positive_amounts, currencies, descriptions)
    @settings(max_examples=50)
    def test_non_positive_amount_raises(
        self, oauth_provider, external_id, amount, currency, description
    ):
        """Non-positive amount always raises ValidationError."""
        with pytest.raises(ValidationError):
            CreateChargeRequest(
                oauth_provider=oauth_provider,
                external_id=external_id,
                amount_minor=amount,
                currency=currency,
                description=description,
            )

    @given(oauth_providers, external_ids, positive_amounts)
    @settings(max_examples=50)
    def test_currency_uppercased(self, oauth_provider, external_id, amount):
        """Currency is always uppercased."""
        request = CreateChargeRequest(
            oauth_provider=oauth_provider,
            external_id=external_id,
            amount_minor=amount,
            currency="usd",
            description="Test",
        )
        assert request.currency == "USD"


# ============================================================================
# API Model Tests - AddCreditsRequest
# ============================================================================


class TestAddCreditsRequestProperties:
    """Property-based tests for AddCreditsRequest API model."""

    @given(
        oauth_providers,
        external_ids,
        positive_amounts,
        currencies,
        descriptions,
        transaction_types,
    )
    @settings(max_examples=100)
    def test_valid_request_creation(
        self, oauth_provider, external_id, amount, currency, description, txn_type
    ):
        """Valid inputs always produce valid AddCreditsRequest."""
        request = AddCreditsRequest(
            oauth_provider=oauth_provider,
            external_id=external_id,
            amount_minor=amount,
            currency=currency,
            description=description,
            transaction_type=txn_type,
        )
        assert request.amount_minor > 0
        assert request.transaction_type in TransactionType

    @given(oauth_providers, external_ids, positive_amounts, currencies, descriptions)
    @settings(max_examples=50)
    def test_serialization_roundtrip(
        self, oauth_provider, external_id, amount, currency, description
    ):
        """Request can be serialized and deserialized."""
        request = AddCreditsRequest(
            oauth_provider=oauth_provider,
            external_id=external_id,
            amount_minor=amount,
            currency=currency,
            description=description,
            transaction_type=TransactionType.GRANT,
        )
        data = request.model_dump()
        restored = AddCreditsRequest(**data)
        assert restored.amount_minor == request.amount_minor


# ============================================================================
# API Model Tests - LiteLLM Models
# ============================================================================


class TestLiteLLMModelsProperties:
    """Property-based tests for LiteLLM API models."""

    @given(oauth_providers, external_ids)
    @settings(max_examples=100)
    def test_auth_request_valid(self, oauth_provider, external_id):
        """Valid inputs produce valid LiteLLMAuthRequest."""
        request = LiteLLMAuthRequest(
            oauth_provider=oauth_provider,
            external_id=external_id,
        )
        assert request.oauth_provider.startswith("oauth:")

    @given(oauth_providers, external_ids, st.text(min_size=1, max_size=100))
    @settings(max_examples=100)
    def test_charge_request_valid(self, oauth_provider, external_id, interaction_id):
        """Valid inputs produce valid LiteLLMChargeRequest."""
        assume(interaction_id.strip())  # Filter empty/whitespace
        request = LiteLLMChargeRequest(
            oauth_provider=oauth_provider,
            external_id=external_id,
            interaction_id=interaction_id,
        )
        assert request.interaction_id

    @given(
        oauth_providers,
        external_ids,
        st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
        st.integers(min_value=0, max_value=100),
        st.integers(min_value=0, max_value=100000),
        st.integers(min_value=0, max_value=100000),
        st.floats(min_value=0, max_value=1000),
        st.integers(min_value=0, max_value=600000),
    )
    @settings(max_examples=100)
    def test_usage_log_request_valid(
        self,
        oauth_provider,
        external_id,
        interaction_id,
        llm_calls,
        prompt_tokens,
        completion_tokens,
        cost_cents,
        duration_ms,
    ):
        """Valid inputs produce valid LiteLLMUsageLogRequest."""
        request = LiteLLMUsageLogRequest(
            oauth_provider=oauth_provider,
            external_id=external_id,
            interaction_id=interaction_id,
            total_llm_calls=llm_calls,
            total_prompt_tokens=prompt_tokens,
            total_completion_tokens=completion_tokens,
            actual_cost_cents=cost_cents,
            duration_ms=duration_ms,
        )
        assert request.total_llm_calls >= 0
        assert request.actual_cost_cents >= 0


# ============================================================================
# API Model Tests - GooglePlayVerifyRequest
# ============================================================================


class TestGooglePlayVerifyRequestProperties:
    """Property-based tests for GooglePlayVerifyRequest API model."""

    @given(
        oauth_providers,
        external_ids,
        st.text(
            min_size=10,
            max_size=500,
            alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789_-"),
        ),
        st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
        st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.filter_too_much])
    def test_valid_request_creation(
        self, oauth_provider, external_id, purchase_token, product_id, package_name
    ):
        """Valid inputs produce valid GooglePlayVerifyRequest."""
        assume(len(purchase_token.strip()) >= 10)
        request = GooglePlayVerifyRequest(
            oauth_provider=oauth_provider,
            external_id=external_id,
            purchase_token=purchase_token,
            product_id=product_id,
            package_name=package_name,
        )
        assert len(request.purchase_token) >= 10

    @given(oauth_providers, external_ids, st.text(max_size=9))
    @settings(max_examples=50)
    def test_short_purchase_token_raises(self, oauth_provider, external_id, short_token):
        """Purchase token under 10 chars always raises ValidationError."""
        with pytest.raises(ValidationError):
            GooglePlayVerifyRequest(
                oauth_provider=oauth_provider,
                external_id=external_id,
                purchase_token=short_token,
                product_id="test_product",
                package_name="com.test.app",
            )


# ============================================================================
# API Model Tests - CreateAccountRequest
# ============================================================================


class TestCreateAccountRequestProperties:
    """Property-based tests for CreateAccountRequest API model."""

    @given(oauth_providers, external_ids, non_negative_amounts, currencies)
    @settings(max_examples=100)
    def test_valid_request_creation(self, oauth_provider, external_id, initial_balance, currency):
        """Valid inputs produce valid CreateAccountRequest."""
        request = CreateAccountRequest(
            oauth_provider=oauth_provider,
            external_id=external_id,
            initial_balance_minor=initial_balance,
            currency=currency,
        )
        assert request.initial_balance_minor >= 0
        assert request.currency == currency.upper()

    @given(oauth_providers, external_ids, st.integers(max_value=-1))
    @settings(max_examples=50)
    def test_negative_initial_balance_raises(self, oauth_provider, external_id, negative_balance):
        """Negative initial balance always raises ValidationError."""
        with pytest.raises(ValidationError):
            CreateAccountRequest(
                oauth_provider=oauth_provider,
                external_id=external_id,
                initial_balance_minor=negative_balance,
            )


# ============================================================================
# Invariant Tests - Cross-Model Properties
# ============================================================================


class TestCrossModelInvariants:
    """Tests for properties that span multiple models."""

    @given(account_identities(), positive_amounts, currencies, descriptions)
    @settings(max_examples=50)
    def test_charge_intent_to_api_request_consistency(
        self, identity, amount, currency, description
    ):
        """ChargeIntent and CreateChargeRequest accept same valid inputs."""
        # Domain model
        intent = ChargeIntent(
            account_identity=identity,
            amount_minor=amount,
            currency=currency,
            description=description,
            metadata=ChargeMetadata(),
            idempotency_key=None,
        )
        # API model
        request = CreateChargeRequest(
            oauth_provider=identity.oauth_provider,
            external_id=identity.external_id,
            wa_id=identity.wa_id,
            tenant_id=identity.tenant_id,
            amount_minor=amount,
            currency=currency,
            description=description,
        )
        assert intent.amount_minor == request.amount_minor
        assert intent.currency == request.currency.upper()

    @given(
        st.integers(min_value=0, max_value=1000),
        st.integers(min_value=0, max_value=100),
        st.integers(min_value=0, max_value=100),
        st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=100)
    def test_credit_check_response_invariants(
        self, credits_remaining, free_uses, daily_uses, daily_limit
    ):
        """CreditCheckResponse maintains logical consistency."""
        has_credit = credits_remaining > 0 or free_uses > 0 or daily_uses > 0
        response = CreditCheckResponse(
            has_credit=has_credit,
            credits_remaining=credits_remaining,
            free_uses_remaining=free_uses,
            daily_free_uses_remaining=daily_uses,
            daily_free_uses_limit=daily_limit,
        )
        # If has_credit is True, at least one credit source should be positive
        if response.has_credit:
            assert (
                response.credits_remaining > 0
                or response.free_uses_remaining > 0
                or response.daily_free_uses_remaining > 0
            )

    @given(charge_intents())
    @settings(max_examples=50)
    def test_charge_intent_identity_extraction(self, intent):
        """ChargeIntent account_identity can be extracted correctly."""
        identity = intent.account_identity
        assert identity.oauth_provider.startswith("oauth:")
        assert identity.external_id


# ============================================================================
# Stateful Testing - Balance Operations
# ============================================================================


class TestBalanceOperationProperties:
    """Property-based tests for balance operation invariants."""

    @given(
        non_negative_amounts,
        st.lists(positive_amounts, min_size=1, max_size=10),
    )
    @settings(max_examples=50)
    def test_credit_additions_monotonic(self, initial_balance, credit_amounts):
        """Adding credits always increases balance."""
        balance = initial_balance
        for amount in credit_amounts:
            new_balance = balance + amount
            assert new_balance > balance
            balance = new_balance

    @given(
        st.integers(min_value=100, max_value=10000),
        st.lists(
            st.integers(min_value=1, max_value=10),
            min_size=1,
            max_size=10,
        ),
    )
    @settings(max_examples=50)
    def test_charge_deductions_monotonic(self, initial_balance, charge_amounts):
        """Deducting charges always decreases balance (when sufficient)."""
        balance = initial_balance
        for amount in charge_amounts:
            if balance >= amount:
                new_balance = balance - amount
                assert new_balance < balance
                balance = new_balance

    @given(
        st.integers(min_value=0, max_value=1000),
        st.lists(
            st.tuples(
                st.sampled_from(["credit", "charge"]),
                st.integers(min_value=1, max_value=100),
            ),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=50)
    def test_balance_never_negative_with_validation(self, initial, operations):
        """Balance should never go negative when validating charges."""
        balance = initial
        for op_type, amount in operations:
            if op_type == "credit":
                balance += amount
            elif op_type == "charge":
                if balance >= amount:
                    balance -= amount
            assert balance >= 0
