#!/usr/bin/env python3
"""
QA Auto-Replenish Script

Automatically replenishes credits for QA test user.
Checks every minute, replenishes if last activity was >30 seconds ago.

Target user: oauth:google / 999888777666555444
"""

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.db.models import Account, Charge
import structlog

logger = structlog.get_logger()

# QA test user to replenish
QA_OAUTH_PROVIDER = "oauth:google"
QA_EXTERNAL_ID = "999888777666555444"
TARGET_FREE_USES = 3
INACTIVITY_THRESHOLD_SECONDS = 30


async def get_qa_account(session: AsyncSession) -> Account | None:
    """Get the QA test account."""
    result = await session.execute(
        select(Account).where(
            Account.oauth_provider == QA_OAUTH_PROVIDER,
            Account.external_id == QA_EXTERNAL_ID
        )
    )
    return result.scalar_one_or_none()


async def get_last_activity_time(session: AsyncSession, account_id: str) -> datetime | None:
    """Get the timestamp of the last charge for this account."""
    result = await session.execute(
        select(Charge.created_at)
        .where(Charge.account_id == account_id)
        .order_by(Charge.created_at.desc())
        .limit(1)
    )
    row = result.first()
    return row[0] if row else None


async def replenish_credits(session: AsyncSession, account: Account) -> None:
    """Replenish the account's free credits back to 3."""
    account.free_uses_remaining = TARGET_FREE_USES
    await session.commit()

    logger.info(
        "qa_credits_replenished",
        account_id=str(account.id),
        external_id=account.external_id,
        free_uses=TARGET_FREE_USES,
        total_uses=account.total_uses
    )


async def check_and_replenish() -> None:
    """Check if replenishment is needed and perform it."""
    # Get database URL from environment
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set in environment")
        return

    # Create async engine and session
    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Get the QA account
        account = await get_qa_account(session)

        if not account:
            logger.warning(
                "qa_account_not_found",
                oauth_provider=QA_OAUTH_PROVIDER,
                external_id=QA_EXTERNAL_ID
            )
            return

        # Check if already at full credits
        if account.free_uses_remaining >= TARGET_FREE_USES:
            logger.debug(
                "qa_account_already_full",
                account_id=str(account.id),
                free_uses=account.free_uses_remaining
            )
            return

        # Get last activity time
        last_activity = await get_last_activity_time(session, account.id)

        if not last_activity:
            # No charges yet, replenish
            logger.info("qa_account_no_activity_replenishing", account_id=str(account.id))
            await replenish_credits(session, account)
            return

        # Check if inactive long enough
        now = datetime.now(timezone.utc)
        time_since_last = (now - last_activity).total_seconds()

        if time_since_last > INACTIVITY_THRESHOLD_SECONDS:
            logger.info(
                "qa_account_inactive_replenishing",
                account_id=str(account.id),
                seconds_since_last_activity=int(time_since_last),
                threshold=INACTIVITY_THRESHOLD_SECONDS
            )
            await replenish_credits(session, account)
        else:
            logger.debug(
                "qa_account_recently_active",
                account_id=str(account.id),
                seconds_since_last_activity=int(time_since_last),
                free_uses_remaining=account.free_uses_remaining
            )

    await engine.dispose()


async def run_loop() -> None:
    """Run the replenishment check in a loop."""
    logger.info("qa_auto_replenish_started", check_interval_seconds=60)

    while True:
        try:
            await check_and_replenish()
        except Exception as e:
            logger.error("qa_replenish_error", error=str(e), exc_info=True)

        # Wait 60 seconds before next check
        await asyncio.sleep(60)


def main():
    """Main entry point."""
    try:
        asyncio.run(run_loop())
    except KeyboardInterrupt:
        logger.info("qa_auto_replenish_stopped")
        sys.exit(0)


if __name__ == "__main__":
    main()
