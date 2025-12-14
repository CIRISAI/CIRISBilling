"""
Product Inventory Service - Manages tool credits per product type.

Handles web search, image generation, and other tool credits separately
from main LLM usage credits.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.config import settings
from app.db.models import Account, ProductInventory, ProductUsageLog
from app.exceptions import InsufficientCreditsError, ResourceNotFoundError
from app.models.domain import AccountIdentity

logger = get_logger(__name__)


@dataclass
class ProductConfig:
    """Configuration for a product type."""

    free_initial: int
    free_daily: int
    price_minor: int  # Cost in cents


# Product configurations - loaded from settings
PRODUCT_CONFIGS: dict[str, ProductConfig] = {
    "web_search": ProductConfig(
        free_initial=settings.product_web_search_free_initial,
        free_daily=settings.product_web_search_free_daily,
        price_minor=settings.product_web_search_price_minor,
    ),
    # Future products:
    # "image_gen": ProductConfig(
    #     free_initial=settings.product_image_gen_free_initial,
    #     free_daily=settings.product_image_gen_free_daily,
    #     price_minor=settings.product_image_gen_price_minor,
    # ),
}


@dataclass
class ProductBalance:
    """Current balance for a product."""

    product_type: str
    free_remaining: int
    paid_credits: int
    total_available: int
    price_minor: int
    total_uses: int


@dataclass
class ProductChargeResult:
    """Result of a product charge operation."""

    success: bool
    used_free: bool
    used_paid: bool
    cost_minor: int
    free_remaining: int
    paid_credits: int
    total_uses: int
    usage_log_id: UUID


class ProductInventoryService:
    """Service for managing product-specific credits."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session."""
        self.session = session

    async def _find_account_by_identity(self, identity: AccountIdentity) -> Account | None:
        """Find account by identity tuple."""
        stmt = select(Account).where(
            Account.oauth_provider == identity.oauth_provider,
            Account.external_id == identity.external_id,
        )

        # Apply optional filters
        if identity.wa_id is not None:
            stmt = stmt.where(Account.wa_id == identity.wa_id)
        else:
            stmt = stmt.where(Account.wa_id.is_(None))

        if identity.tenant_id is not None:
            stmt = stmt.where(Account.tenant_id == identity.tenant_id)
        else:
            stmt = stmt.where(Account.tenant_id.is_(None))

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create_inventory(
        self, account_id: UUID, product_type: str
    ) -> ProductInventory:
        """Get or create product inventory for an account."""
        if product_type not in PRODUCT_CONFIGS:
            raise ValueError(f"Unknown product type: {product_type}")

        config = PRODUCT_CONFIGS[product_type]

        # Try to find existing inventory
        stmt = select(ProductInventory).where(
            ProductInventory.account_id == account_id,
            ProductInventory.product_type == product_type,
        )
        result = await self.session.execute(stmt)
        inventory = result.scalar_one_or_none()

        if inventory is None:
            # Create new inventory with initial free credits
            inventory = ProductInventory(
                account_id=account_id,
                product_type=product_type,
                free_remaining=config.free_initial,
                paid_credits=0,
                last_daily_refresh=datetime.now(UTC),
                total_uses=0,
            )
            self.session.add(inventory)
            await self.session.flush()

            logger.info(
                "product_inventory_created",
                account_id=str(account_id),
                product_type=product_type,
                free_initial=config.free_initial,
            )

        return inventory

    def _should_refresh_daily(self, inventory: ProductInventory) -> bool:
        """Check if daily free credits should be refreshed."""
        if inventory.last_daily_refresh is None:
            return True

        now = datetime.now(UTC)
        last_refresh = inventory.last_daily_refresh

        # Refresh if it's a new day (UTC)
        return now.date() > last_refresh.date()

    async def _refresh_daily_credits(self, inventory: ProductInventory) -> bool:
        """Refresh daily free credits if needed. Returns True if refreshed."""
        if not self._should_refresh_daily(inventory):
            return False

        config = PRODUCT_CONFIGS[inventory.product_type]

        # Add daily free credits (don't exceed initial amount)
        old_free = inventory.free_remaining
        inventory.free_remaining = min(
            inventory.free_remaining + config.free_daily,
            config.free_initial + config.free_daily,  # Cap at initial + 1 day
        )
        inventory.last_daily_refresh = datetime.now(UTC)

        logger.info(
            "product_daily_credits_refreshed",
            account_id=str(inventory.account_id),
            product_type=inventory.product_type,
            old_free=old_free,
            new_free=inventory.free_remaining,
            daily_added=config.free_daily,
        )

        return True

    async def get_balance(self, identity: AccountIdentity, product_type: str) -> ProductBalance:
        """Get current balance for a product."""
        account = await self._find_account_by_identity(identity)
        if account is None:
            raise ResourceNotFoundError(f"Account not found for identity: {identity}")

        inventory = await self.get_or_create_inventory(account.id, product_type)

        # Check for daily refresh
        await self._refresh_daily_credits(inventory)

        config = PRODUCT_CONFIGS[product_type]

        return ProductBalance(
            product_type=product_type,
            free_remaining=inventory.free_remaining,
            paid_credits=inventory.paid_credits,
            total_available=inventory.free_remaining + inventory.paid_credits,
            price_minor=config.price_minor,
            total_uses=inventory.total_uses,
        )

    async def check_credit(self, identity: AccountIdentity, product_type: str) -> bool:
        """Check if user has any credit (free or paid) for a product."""
        try:
            balance = await self.get_balance(identity, product_type)
            return balance.total_available > 0
        except ResourceNotFoundError:
            return False

    async def charge(
        self,
        identity: AccountIdentity,
        product_type: str,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> ProductChargeResult:
        """
        Charge one unit of a product.

        Uses free credits first, then paid credits.
        Raises InsufficientCreditsError if no credits available.
        """
        account = await self._find_account_by_identity(identity)
        if account is None:
            raise ResourceNotFoundError(f"Account not found for identity: {identity}")

        inventory = await self.get_or_create_inventory(account.id, product_type)
        config = PRODUCT_CONFIGS[product_type]

        # Check for idempotency
        if idempotency_key:
            existing_stmt = select(ProductUsageLog).where(
                ProductUsageLog.account_id == account.id,
                ProductUsageLog.idempotency_key == idempotency_key,
            )
            existing_result = await self.session.execute(existing_stmt)
            existing_log = existing_result.scalar_one_or_none()

            if existing_log:
                logger.debug(
                    "product_charge_idempotent_hit",
                    account_id=str(account.id),
                    product_type=product_type,
                    idempotency_key=idempotency_key,
                )
                return ProductChargeResult(
                    success=True,
                    used_free=existing_log.used_free,
                    used_paid=existing_log.used_paid,
                    cost_minor=existing_log.cost_minor,
                    free_remaining=inventory.free_remaining,
                    paid_credits=inventory.paid_credits,
                    total_uses=inventory.total_uses,
                    usage_log_id=existing_log.id,
                )

        # Check for daily refresh
        await self._refresh_daily_credits(inventory)

        # Snapshot before
        free_before = inventory.free_remaining
        paid_before = inventory.paid_credits

        # Determine credit source
        used_free = False
        used_paid = False
        cost_minor = 0

        if inventory.free_remaining > 0:
            # Use free credit
            inventory.free_remaining -= 1
            used_free = True
            cost_minor = 0
        elif inventory.paid_credits > 0:
            # Use paid credit
            inventory.paid_credits -= 1
            used_paid = True
            cost_minor = config.price_minor
        else:
            # No credits available
            raise InsufficientCreditsError(
                balance=inventory.free_remaining + inventory.paid_credits,
                required=1,
            )

        # Increment usage counter
        inventory.total_uses += 1

        # Create usage log
        usage_log = ProductUsageLog(
            account_id=account.id,
            product_type=product_type,
            used_free=used_free,
            used_paid=used_paid,
            cost_minor=cost_minor,
            free_before=free_before,
            free_after=inventory.free_remaining,
            paid_before=paid_before,
            paid_after=inventory.paid_credits,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        self.session.add(usage_log)

        await self.session.flush()
        await self.session.commit()

        logger.info(
            "product_charge_success",
            account_id=str(account.id),
            product_type=product_type,
            used_free=used_free,
            used_paid=used_paid,
            cost_minor=cost_minor,
            free_remaining=inventory.free_remaining,
            paid_credits=inventory.paid_credits,
            total_uses=inventory.total_uses,
        )

        return ProductChargeResult(
            success=True,
            used_free=used_free,
            used_paid=used_paid,
            cost_minor=cost_minor,
            free_remaining=inventory.free_remaining,
            paid_credits=inventory.paid_credits,
            total_uses=inventory.total_uses,
            usage_log_id=usage_log.id,
        )

    async def add_credits(
        self,
        identity: AccountIdentity,
        product_type: str,
        credits: int,
        source: str = "purchase",
    ) -> ProductBalance:
        """Add paid credits to a product inventory."""
        account = await self._find_account_by_identity(identity)
        if account is None:
            raise ResourceNotFoundError(f"Account not found for identity: {identity}")

        inventory = await self.get_or_create_inventory(account.id, product_type)

        old_paid = inventory.paid_credits
        inventory.paid_credits += credits

        await self.session.flush()
        await self.session.commit()

        logger.info(
            "product_credits_added",
            account_id=str(account.id),
            product_type=product_type,
            credits_added=credits,
            old_paid=old_paid,
            new_paid=inventory.paid_credits,
            source=source,
        )

        config = PRODUCT_CONFIGS[product_type]

        return ProductBalance(
            product_type=product_type,
            free_remaining=inventory.free_remaining,
            paid_credits=inventory.paid_credits,
            total_available=inventory.free_remaining + inventory.paid_credits,
            price_minor=config.price_minor,
            total_uses=inventory.total_uses,
        )

    async def get_all_balances(self, identity: AccountIdentity) -> list[ProductBalance]:
        """Get balances for all product types for an account."""
        account = await self._find_account_by_identity(identity)
        if account is None:
            raise ResourceNotFoundError(f"Account not found for identity: {identity}")

        balances = []
        for product_type in PRODUCT_CONFIGS:
            inventory = await self.get_or_create_inventory(account.id, product_type)
            await self._refresh_daily_credits(inventory)
            config = PRODUCT_CONFIGS[product_type]

            balances.append(
                ProductBalance(
                    product_type=product_type,
                    free_remaining=inventory.free_remaining,
                    paid_credits=inventory.paid_credits,
                    total_available=inventory.free_remaining + inventory.paid_credits,
                    price_minor=config.price_minor,
                    total_uses=inventory.total_uses,
                )
            )

        return balances
