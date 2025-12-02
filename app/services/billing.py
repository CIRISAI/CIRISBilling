"""
Billing Service - Core business logic with write verification.

NO DICTIONARIES - All operations use strongly typed domain models.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, Charge, Credit, CreditCheck
from app.exceptions import (
    AccountClosedError,
    AccountNotFoundError,
    AccountSuspendedError,
    DataIntegrityError,
    IdempotencyConflictError,
    InsufficientCreditsError,
    WriteVerificationError,
)
from app.models.api import (
    AccountStatus,
    ChargeMetadata,
    CreditCheckContext,
    CreditCheckResponse,
    TransactionType,
)
from app.models.domain import (
    AccountData,
    AccountIdentity,
    ChargeData,
    ChargeIntent,
    CreditData,
    CreditIntent,
)


def _utc_now() -> datetime:
    """Get current UTC timestamp."""
    return datetime.now(UTC)


def _get_next_reset_time() -> datetime:
    """Get the next daily reset time (midnight UTC)."""
    now = _utc_now()
    tomorrow = now.date() + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=UTC)


def _should_reset_daily_uses(reset_at: datetime | None) -> bool:
    """Check if daily free uses should be reset."""
    if reset_at is None:
        return True
    return _utc_now() >= reset_at


class BillingService:
    """
    Billing service with write verification.

    All write operations follow the pattern:
    1. Execute write
    2. Flush to database
    3. Read back and verify
    4. Validate invariants
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize billing service with database session."""
        self.session = session

    async def check_credit(
        self,
        identity: AccountIdentity,
        context: CreditCheckContext | None = None,
        customer_email: str | None = None,
        marketing_opt_in: bool = False,
        marketing_opt_in_source: str | None = None,
        user_role: str | None = None,
        agent_id: str | None = None,
    ) -> CreditCheckResponse:
        """
        Check if account has sufficient credits (free or paid).

        Auto-creates new accounts with free credits on first check.
        Logs the check for audit purposes.
        """
        from app.config import settings

        # Find account
        account = await self._find_account_by_identity(identity)

        # Account doesn't exist - create with free credits
        if account is None:
            # Create new account with free uses
            new_account = Account(
                oauth_provider=identity.oauth_provider,
                external_id=identity.external_id,
                wa_id=identity.wa_id,
                tenant_id=identity.tenant_id,
                customer_email=customer_email,
                balance_minor=0,
                currency="USD",
                plan_name="free",
                free_uses_remaining=settings.free_uses_per_account,
                total_uses=0,
                marketing_opt_in=marketing_opt_in,
                marketing_opt_in_at=_utc_now() if marketing_opt_in else None,
                marketing_opt_in_source=marketing_opt_in_source,
                user_role=user_role,
                agent_id=agent_id,
            )
            # Set status explicitly after creation to avoid enum conversion issues
            new_account.status = "active"

            self.session.add(new_account)

            try:
                await self.session.flush()
                await self.session.commit()
            except IntegrityError as e:
                # Race condition - account created by another request
                from structlog import get_logger

                logger = get_logger(__name__)
                logger.error(
                    "account_creation_integrity_error", error=str(e), identity=str(identity)
                )
                await self.session.rollback()
                account = await self._find_account_by_identity(identity)
                if account is None:
                    raise WriteVerificationError(f"Account creation failed: {str(e)}")
            else:
                # Successfully created
                account = new_account

        # Log credit check for auditing
        await self._log_credit_check(identity, context, account)

        # Account suspended
        if account.status == AccountStatus.SUSPENDED:
            return CreditCheckResponse(
                has_credit=False,
                credits_remaining=account.paid_credits,
                plan_name=account.plan_name,
                reason="Account suspended",
                free_uses_remaining=account.free_uses_remaining,
                total_uses=account.total_uses,
                daily_free_uses_remaining=0,
                daily_free_uses_limit=account.daily_free_uses_limit,
            )

        # Account closed
        if account.status == AccountStatus.CLOSED:
            return CreditCheckResponse(
                has_credit=False,
                credits_remaining=account.paid_credits,
                plan_name=account.plan_name,
                reason="Account closed",
                free_uses_remaining=account.free_uses_remaining,
                total_uses=account.total_uses,
                daily_free_uses_remaining=0,
                daily_free_uses_limit=account.daily_free_uses_limit,
            )

        # Reset daily uses if needed
        daily_free_uses = account.daily_free_uses_remaining
        if _should_reset_daily_uses(account.daily_free_uses_reset_at):
            daily_free_uses = account.daily_free_uses_limit
            # Update the account with reset values (will be saved lazily)
            account.daily_free_uses_remaining = daily_free_uses
            account.daily_free_uses_reset_at = _get_next_reset_time()
            await self.session.flush()
            await self.session.commit()

        # Check if account has credit (daily free, one-time free, or paid)
        has_daily_free = daily_free_uses > 0
        has_free_uses = account.free_uses_remaining > 0
        has_paid_credits = account.paid_credits > 0
        has_credit = has_daily_free or has_free_uses or has_paid_credits

        # Determine if purchase is required
        purchase_required = not has_daily_free and not has_free_uses and not has_paid_credits

        return CreditCheckResponse(
            has_credit=has_credit,
            credits_remaining=account.paid_credits,
            plan_name=account.plan_name,
            reason=None if has_credit else "No free uses or credits remaining",
            free_uses_remaining=account.free_uses_remaining,
            total_uses=account.total_uses,
            purchase_required=purchase_required,
            purchase_price_minor=settings.price_per_purchase_minor if purchase_required else 0,
            purchase_uses=settings.paid_uses_per_purchase if purchase_required else 0,
            daily_free_uses_remaining=daily_free_uses,
            daily_free_uses_limit=account.daily_free_uses_limit,
        )

    async def create_charge(self, intent: ChargeIntent) -> ChargeData:
        """
        Create a charge (deduct credits from account).

        This operation requires:
        1. Row-level locking (SELECT FOR UPDATE)
        2. Balance verification
        3. Atomic balance update
        4. Write verification

        Raises:
            AccountNotFoundError: Account doesn't exist
            AccountSuspendedError: Account is suspended
            AccountClosedError: Account is closed
            InsufficientCreditsError: Insufficient balance
            IdempotencyConflictError: Duplicate idempotency key
        """
        # Check for duplicate idempotency key first
        if intent.idempotency_key:
            existing_charge = await self._find_charge_by_idempotency(intent.idempotency_key)
            if existing_charge:
                raise IdempotencyConflictError(existing_charge.id)

        # Lock account row for update
        account = await self._lock_account_for_update(intent.account_identity)

        if account is None:
            raise AccountNotFoundError(intent.account_identity)

        # Verify account status
        if account.status == AccountStatus.SUSPENDED:
            raise AccountSuspendedError(account.id, "Account suspended")

        if account.status == AccountStatus.CLOSED:
            raise AccountClosedError(account.id)

        # Verify currency matches
        if account.currency != intent.currency:
            raise DataIntegrityError(
                f"Currency mismatch: account={account.currency}, charge={intent.currency}"
            )

        # Reset daily uses if needed (before checking availability)
        if _should_reset_daily_uses(account.daily_free_uses_reset_at):
            account.daily_free_uses_remaining = account.daily_free_uses_limit
            account.daily_free_uses_reset_at = _get_next_reset_time()

        # Determine what type of credit to use (priority: daily free > one-time free > paid)
        using_daily_free = account.daily_free_uses_remaining > 0
        using_free_use = not using_daily_free and account.free_uses_remaining > 0

        # Track what gets deducted
        daily_free_before = account.daily_free_uses_remaining
        daily_free_after = daily_free_before
        free_uses_before = account.free_uses_remaining
        free_uses_after = free_uses_before
        credits_before = account.paid_credits
        credits_after = credits_before

        if using_daily_free:
            # Use daily free - don't charge anything else
            daily_free_after = daily_free_before - 1
        elif using_free_use:
            # Use one-time free tier - don't charge paid credits
            free_uses_after = free_uses_before - 1
        else:
            # Use paid credits - deduct from paid_credits
            if account.paid_credits < intent.amount_minor:
                raise InsufficientCreditsError(account.paid_credits, intent.amount_minor)
            credits_after = credits_before - intent.amount_minor

        # Create charge record
        charge = Charge(
            account_id=account.id,
            amount_minor=intent.amount_minor,
            currency=intent.currency,
            balance_before=credits_before,
            balance_after=credits_after,
            description=intent.description,
            idempotency_key=intent.idempotency_key,
            metadata_message_id=intent.metadata.message_id,
            metadata_agent_id=intent.metadata.agent_id,
            metadata_channel_id=intent.metadata.channel_id,
            metadata_request_id=intent.metadata.request_id,
        )

        self.session.add(charge)
        await self.session.flush()

        # Verify charge was written
        verified_charge = await self.session.get(Charge, charge.id)
        if verified_charge is None:
            raise WriteVerificationError(f"Charge {charge.id} not found after insert")

        # Update account balances and total uses
        account.paid_credits = credits_after
        account.free_uses_remaining = free_uses_after
        account.daily_free_uses_remaining = daily_free_after
        account.total_uses = account.total_uses + 1
        await self.session.flush()

        # Verify account was updated
        verified_account = await self.session.get(Account, account.id)
        if verified_account is None:
            raise WriteVerificationError(f"Account {account.id} disappeared after update")

        if verified_account.paid_credits != credits_after:
            raise DataIntegrityError(
                f"Paid credits mismatch: expected {credits_after}, got {verified_account.paid_credits}"
            )

        if verified_account.free_uses_remaining != free_uses_after:
            raise DataIntegrityError(
                f"Free uses mismatch: expected {free_uses_after}, got {verified_account.free_uses_remaining}"
            )

        if verified_account.daily_free_uses_remaining != daily_free_after:
            raise DataIntegrityError(
                f"Daily free uses mismatch: expected {daily_free_after}, got {verified_account.daily_free_uses_remaining}"
            )

        # Commit transaction
        await self.session.commit()

        return ChargeData(
            charge_id=verified_charge.id,
            account_id=verified_charge.account_id,
            amount_minor=verified_charge.amount_minor,
            currency=verified_charge.currency,
            balance_before=verified_charge.balance_before,
            balance_after=verified_charge.balance_after,
            description=verified_charge.description,
            metadata=ChargeMetadata(
                message_id=verified_charge.metadata_message_id,
                agent_id=verified_charge.metadata_agent_id,
                channel_id=verified_charge.metadata_channel_id,
                request_id=verified_charge.metadata_request_id,
            ),
            created_at=verified_charge.created_at,
        )

    async def add_credits(self, intent: CreditIntent) -> CreditData:
        """
        Add credits to account (purchase, grant, refund).

        This operation requires:
        1. Row-level locking
        2. Atomic balance update
        3. Write verification

        Raises:
            AccountNotFoundError: Account doesn't exist
            IdempotencyConflictError: Duplicate idempotency key
        """
        # Check for duplicate idempotency key first
        if intent.idempotency_key:
            existing_credit = await self._find_credit_by_idempotency(intent.idempotency_key)
            if existing_credit:
                raise IdempotencyConflictError(existing_credit.id)

        # Lock account row for update
        account = await self._lock_account_for_update(intent.account_identity)

        if account is None:
            raise AccountNotFoundError(intent.account_identity)

        # Verify currency matches
        if account.currency != intent.currency:
            raise DataIntegrityError(
                f"Currency mismatch: account={account.currency}, credit={intent.currency}"
            )

        # Calculate new paid credits balance
        credits_before = account.paid_credits
        credits_after = credits_before + intent.amount_minor

        # Create credit record
        credit = Credit(
            account_id=account.id,
            amount_minor=intent.amount_minor,
            currency=intent.currency,
            balance_before=credits_before,
            balance_after=credits_after,
            transaction_type=intent.transaction_type,
            description=intent.description,
            external_transaction_id=intent.external_transaction_id,
            idempotency_key=intent.idempotency_key,
        )

        self.session.add(credit)
        await self.session.flush()

        # Verify credit was written
        verified_credit = await self.session.get(Credit, credit.id)
        if verified_credit is None:
            raise WriteVerificationError(f"Credit {credit.id} not found after insert")

        # Update account paid credits
        account.paid_credits = credits_after
        await self.session.flush()

        # Verify paid credits was updated
        verified_account = await self.session.get(Account, account.id)
        if verified_account is None:
            raise WriteVerificationError(f"Account {account.id} disappeared after update")

        if verified_account.paid_credits != credits_after:
            raise DataIntegrityError(
                f"Paid credits mismatch: expected {credits_after}, got {verified_account.paid_credits}"
            )

        # Commit transaction
        await self.session.commit()

        return CreditData(
            credit_id=verified_credit.id,
            account_id=verified_credit.account_id,
            amount_minor=verified_credit.amount_minor,
            currency=verified_credit.currency,
            balance_before=verified_credit.balance_before,
            balance_after=verified_credit.balance_after,
            transaction_type=verified_credit.transaction_type,
            description=verified_credit.description,
            external_transaction_id=verified_credit.external_transaction_id,
            created_at=verified_credit.created_at,
        )

    async def get_or_create_account(
        self,
        identity: AccountIdentity,
        initial_balance_minor: int = 0,
        currency: str = "USD",
        plan_name: str = "free",
        customer_email: str | None = None,
        display_name: str | None = None,
        marketing_opt_in: bool = False,
        marketing_opt_in_source: str | None = None,
        user_role: str | None = None,
        agent_id: str | None = None,
    ) -> AccountData:
        """
        Get existing account or create new one (upsert).

        Returns existing account if found, otherwise creates new one.
        """
        # Try to find existing account
        account = await self._find_account_by_identity(identity)

        if account is not None:
            # Account exists - return it
            return self._account_to_domain(account)

        # Create new account
        new_account = Account(
            oauth_provider=identity.oauth_provider,
            external_id=identity.external_id,
            wa_id=identity.wa_id,
            tenant_id=identity.tenant_id,
            customer_email=customer_email,
            display_name=display_name,
            balance_minor=initial_balance_minor,
            currency=currency,
            plan_name=plan_name,
            status=AccountStatus.ACTIVE,
            marketing_opt_in=marketing_opt_in,
            marketing_opt_in_at=_utc_now() if marketing_opt_in else None,
            marketing_opt_in_source=marketing_opt_in_source,
            user_role=user_role,
            agent_id=agent_id,
        )

        self.session.add(new_account)

        try:
            await self.session.flush()
        except IntegrityError:
            # Race condition - account created by another request
            await self.session.rollback()
            account = await self._find_account_by_identity(identity)
            if account is None:
                raise WriteVerificationError("Account creation failed due to race condition")
            return self._account_to_domain(account)

        # Verify account was written
        verified_account = await self.session.get(Account, new_account.id)
        if verified_account is None:
            raise WriteVerificationError(f"Account {new_account.id} not found after insert")

        await self.session.commit()

        return self._account_to_domain(verified_account)

    async def get_account(self, identity: AccountIdentity) -> AccountData:
        """
        Get account by identity.

        Raises:
            AccountNotFoundError: Account doesn't exist
        """
        account = await self._find_account_by_identity(identity)
        if account is None:
            raise AccountNotFoundError(identity)
        return self._account_to_domain(account)

    async def update_account_metadata(
        self,
        identity: AccountIdentity,
        customer_email: str | None = None,
        display_name: str | None = None,
        marketing_opt_in: bool | None = None,
        marketing_opt_in_source: str | None = None,
        user_role: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        """
        Update account metadata fields if provided.

        Only updates fields that are not None.
        """
        from structlog import get_logger

        logger = get_logger(__name__)
        logger.info(
            "update_account_metadata_called",
            oauth_provider=identity.oauth_provider,
            external_id=identity.external_id,
            customer_email=customer_email,
            display_name=display_name,
            marketing_opt_in=marketing_opt_in,
            user_role=user_role,
            agent_id=agent_id,
        )

        account = await self._find_account_by_identity(identity)
        if account is None:
            logger.warning("update_account_metadata_no_account", identity=str(identity))
            return  # Account doesn't exist, nothing to update

        updated = False

        if customer_email is not None and account.customer_email != customer_email:
            logger.info(
                "update_account_metadata_email_update",
                old_email=account.customer_email,
                new_email=customer_email,
            )
            account.customer_email = customer_email
            updated = True
        elif customer_email is not None:
            logger.info(
                "update_account_metadata_email_unchanged",
                email=customer_email,
            )

        if display_name is not None and account.display_name != display_name:
            logger.info(
                "update_account_metadata_name_update",
                old_name=account.display_name,
                new_name=display_name,
            )
            account.display_name = display_name
            updated = True

        if marketing_opt_in is not None and account.marketing_opt_in != marketing_opt_in:
            account.marketing_opt_in = marketing_opt_in
            account.marketing_opt_in_at = _utc_now() if marketing_opt_in else None
            if marketing_opt_in_source is not None:
                account.marketing_opt_in_source = marketing_opt_in_source
            updated = True

        if user_role is not None and account.user_role != user_role:
            account.user_role = user_role
            updated = True

        if agent_id is not None and account.agent_id != agent_id:
            account.agent_id = agent_id
            updated = True

        if updated:
            await self.session.flush()
            await self.session.commit()
            logger.info(
                "update_account_metadata_committed",
                oauth_provider=identity.oauth_provider,
                external_id=identity.external_id,
            )
        else:
            logger.info(
                "update_account_metadata_no_changes",
                oauth_provider=identity.oauth_provider,
                external_id=identity.external_id,
            )

    async def add_purchased_uses(
        self,
        identity: AccountIdentity,
        uses_to_add: int,
        payment_id: str,
        amount_paid_minor: int,
    ) -> AccountData:
        """
        Add purchased uses to account balance.

        This is called after successful Stripe payment.
        Uses are added as balance (in minor units).

        Args:
            identity: Account identity
            uses_to_add: Number of uses purchased
            payment_id: Stripe payment ID
            amount_paid_minor: Amount paid in minor units

        Returns:
            Updated account data

        Raises:
            AccountNotFoundError: Account doesn't exist
        """
        # Lock account for update
        account = await self._lock_account_for_update(identity)
        if account is None:
            raise AccountNotFoundError(identity)

        # Add uses as credits to balance
        # Each use is 1 credit, so 20 uses = 20 credits
        # The balance represents available uses, not dollar amounts
        credit_amount = uses_to_add

        # Add credits using existing add_credits method
        credit_intent = CreditIntent(
            account_identity=identity,
            amount_minor=credit_amount,  # Adding uses, not cents
            currency="USD",
            transaction_type=TransactionType.PURCHASE,
            description=f"Purchased ${amount_paid_minor/100:.2f} ({uses_to_add} uses) via Stripe",
            external_transaction_id=payment_id,
            idempotency_key=f"stripe-{payment_id}",
        )

        await self.add_credits(credit_intent)

        # Return updated account
        updated_account = await self._find_account_by_identity(identity)
        if updated_account is None:
            raise WriteVerificationError("Account disappeared after purchase")

        return self._account_to_domain(updated_account)

    # ========================================================================
    # Private Helper Methods
    # ========================================================================

    async def _find_account_by_identity(self, identity: AccountIdentity) -> Account | None:
        """
        Find account by identity fields.

        Primary match: oauth_provider + external_id (these uniquely identify a user)
        Optional fields: wa_id and tenant_id are ignored for lookup
        """
        stmt = select(Account).where(
            Account.oauth_provider == identity.oauth_provider,
            Account.external_id == identity.external_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _lock_account_for_update(self, identity: AccountIdentity) -> Account | None:
        """
        Lock account row for update (SELECT FOR UPDATE).

        Primary match: oauth_provider + external_id (these uniquely identify a user)
        Optional fields: wa_id and tenant_id are ignored for lookup
        """
        stmt = (
            select(Account)
            .where(
                Account.oauth_provider == identity.oauth_provider,
                Account.external_id == identity.external_id,
            )
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _find_charge_by_idempotency(self, idempotency_key: str) -> Charge | None:
        """Find charge by idempotency key."""
        stmt = select(Charge).where(Charge.idempotency_key == idempotency_key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _find_credit_by_idempotency(self, idempotency_key: str) -> Credit | None:
        """Find credit by idempotency key."""
        stmt = select(Credit).where(Credit.idempotency_key == idempotency_key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _log_credit_check(
        self,
        identity: AccountIdentity,
        context: CreditCheckContext | None,
        account: Account | None,
    ) -> None:
        """Log credit check for auditing."""
        check = CreditCheck(
            account_id=account.id if account else None,
            oauth_provider=identity.oauth_provider,
            external_id=identity.external_id,
            wa_id=identity.wa_id,
            tenant_id=identity.tenant_id,
            has_credit=account.paid_credits > 0 if account else False,
            credits_remaining=account.paid_credits if account else None,
            plan_name=account.plan_name if account else None,
            denial_reason=(
                None
                if account and account.paid_credits > 0
                else ("Insufficient credits" if account else "Account not found")
            ),
            context_agent_id=context.agent_id if context else None,
            context_channel_id=context.channel_id if context else None,
            context_request_id=context.request_id if context else None,
        )
        self.session.add(check)
        # Don't wait for flush - this is fire-and-forget logging

    def _account_to_domain(self, account: Account) -> AccountData:
        """Convert ORM account to domain model."""
        # Check if daily free uses need reset
        daily_free_uses = account.daily_free_uses_remaining
        daily_reset_at = account.daily_free_uses_reset_at
        if _should_reset_daily_uses(daily_reset_at):
            daily_free_uses = account.daily_free_uses_limit
            daily_reset_at = _get_next_reset_time()

        return AccountData(
            account_id=account.id,
            oauth_provider=account.oauth_provider,
            external_id=account.external_id,
            wa_id=account.wa_id,
            tenant_id=account.tenant_id,
            customer_email=account.customer_email,
            balance_minor=account.balance_minor,
            currency=account.currency,
            plan_name=account.plan_name,
            status=AccountStatus(account.status),
            paid_credits=account.paid_credits,
            marketing_opt_in=account.marketing_opt_in,
            marketing_opt_in_at=account.marketing_opt_in_at,
            marketing_opt_in_source=account.marketing_opt_in_source,
            created_at=account.created_at,
            updated_at=account.updated_at,
            free_uses_remaining=account.free_uses_remaining,
            daily_free_uses_remaining=daily_free_uses,
            daily_free_uses_limit=account.daily_free_uses_limit,
            daily_free_uses_reset_at=daily_reset_at,
        )
