-- Test Data Initialization for CIRIS Billing API
-- Creates sample accounts, charges, and credits for local testing

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Insert test accounts
INSERT INTO accounts (id, oauth_provider, external_id, wa_id, tenant_id, balance_minor, currency, plan_name, status, free_uses_remaining, total_uses, created_at, updated_at)
VALUES
    -- Account 1: Active account with balance (used all free uses, now on paid)
    (
        '550e8400-e29b-41d4-a716-446655440000',
        'oauth:google',
        'test-user-1@example.com',
        'wa-test-001',
        'tenant-acme',
        5000,
        'USD',
        'pro',
        'active',
        0,  -- All free uses exhausted
        25, -- Has used 25 total times
        NOW() - INTERVAL '30 days',
        NOW()
    ),
    -- Account 2: Active account with low balance (1 free use remaining)
    (
        '550e8400-e29b-41d4-a716-446655440001',
        'oauth:google',
        'test-user-2@example.com',
        'wa-test-002',
        'tenant-acme',
        50,
        'USD',
        'free',
        'active',
        1,  -- 1 free use remaining
        10, -- Has used 10 total times
        NOW() - INTERVAL '15 days',
        NOW()
    ),
    -- Account 3: Account with zero balance (all free uses remaining, never used)
    (
        '550e8400-e29b-41d4-a716-446655440002',
        'oauth:discord',
        'discord-user-123456',
        NULL,
        NULL,
        0,
        'USD',
        'free',
        'active',
        3,  -- All 3 free uses available
        0,  -- Never used
        NOW() - INTERVAL '7 days',
        NOW()
    ),
    -- Account 4: Suspended account
    (
        '550e8400-e29b-41d4-a716-446655440003',
        'oauth:google',
        'suspended-user@example.com',
        'wa-test-003',
        'tenant-acme',
        1000,
        'USD',
        'pro',
        'suspended',
        0,  -- No free uses (suspended)
        50, -- Has used 50 total times
        NOW() - INTERVAL '60 days',
        NOW()
    ),
    -- Account 5: Account with high balance (whale)
    (
        '550e8400-e29b-41d4-a716-446655440004',
        'oauth:google',
        'whale-user@example.com',
        'wa-test-004',
        'tenant-enterprise',
        100000,
        'USD',
        'enterprise',
        'active',
        0,   -- No free uses (enterprise)
        500, -- Heavy usage
        NOW() - INTERVAL '90 days',
        NOW()
    )
ON CONFLICT (oauth_provider, external_id, wa_id, tenant_id) DO NOTHING;

-- Insert historical credits (account funding)
INSERT INTO credits (id, account_id, amount_minor, currency, balance_before, balance_after, transaction_type, description, external_transaction_id, idempotency_key, created_at)
VALUES
    -- Account 1 credits
    (
        uuid_generate_v4(),
        '550e8400-e29b-41d4-a716-446655440000',
        5000,
        'USD',
        0,
        5000,
        'purchase',
        'Initial subscription purchase',
        'stripe_pi_test_001',
        'credit-test-001',
        NOW() - INTERVAL '30 days'
    ),
    (
        uuid_generate_v4(),
        '550e8400-e29b-41d4-a716-446655440000',
        2000,
        'USD',
        3000,
        5000,
        'purchase',
        'Monthly subscription renewal',
        'stripe_pi_test_002',
        'credit-test-002',
        NOW() - INTERVAL '2 days'
    ),
    -- Account 2 credits
    (
        uuid_generate_v4(),
        '550e8400-e29b-41d4-a716-446655440001',
        1000,
        'USD',
        0,
        1000,
        'grant',
        'Welcome bonus',
        NULL,
        'credit-test-003',
        NOW() - INTERVAL '15 days'
    ),
    -- Account 4 credits (suspended)
    (
        uuid_generate_v4(),
        '550e8400-e29b-41d4-a716-446655440003',
        1000,
        'USD',
        0,
        1000,
        'purchase',
        'Initial purchase',
        'stripe_pi_test_004',
        'credit-test-004',
        NOW() - INTERVAL '60 days'
    ),
    -- Account 5 credits (whale)
    (
        uuid_generate_v4(),
        '550e8400-e29b-41d4-a716-446655440004',
        100000,
        'USD',
        0,
        100000,
        'purchase',
        'Enterprise annual subscription',
        'stripe_pi_test_005',
        'credit-test-005',
        NOW() - INTERVAL '90 days'
    )
ON CONFLICT (account_id, idempotency_key) DO NOTHING;

-- Insert historical charges (usage)
INSERT INTO charges (id, account_id, amount_minor, currency, balance_before, balance_after, description, idempotency_key, metadata_message_id, metadata_agent_id, metadata_channel_id, metadata_request_id, created_at)
VALUES
    -- Account 1 charges
    (
        uuid_generate_v4(),
        '550e8400-e29b-41d4-a716-446655440000',
        100,
        'USD',
        5000,
        4900,
        'Agent interaction - datum',
        'charge-test-001',
        'msg-001',
        'datum',
        'discord:12345',
        'req-001',
        NOW() - INTERVAL '5 days'
    ),
    (
        uuid_generate_v4(),
        '550e8400-e29b-41d4-a716-446655440000',
        150,
        'USD',
        4900,
        4750,
        'Agent interaction - datum',
        'charge-test-002',
        'msg-002',
        'datum',
        'slack:67890',
        'req-002',
        NOW() - INTERVAL '4 days'
    ),
    (
        uuid_generate_v4(),
        '550e8400-e29b-41d4-a716-446655440000',
        200,
        'USD',
        4750,
        4550,
        'Agent interaction - datum',
        'charge-test-003',
        'msg-003',
        'datum',
        'web:session-123',
        'req-003',
        NOW() - INTERVAL '3 days'
    ),
    -- Account 2 charges (almost depleted)
    (
        uuid_generate_v4(),
        '550e8400-e29b-41d4-a716-446655440001',
        950,
        'USD',
        1000,
        50,
        'Agent interaction - datum',
        'charge-test-004',
        'msg-004',
        'datum',
        'discord:99999',
        'req-004',
        NOW() - INTERVAL '10 days'
    ),
    -- Account 5 charges (whale usage)
    (
        uuid_generate_v4(),
        '550e8400-e29b-41d4-a716-446655440004',
        500,
        'USD',
        100000,
        99500,
        'Bulk API usage',
        'charge-test-005',
        'msg-005',
        'datum',
        'api:enterprise-001',
        'req-005',
        NOW() - INTERVAL '1 day'
    )
ON CONFLICT (account_id, idempotency_key) DO NOTHING;

-- Insert some credit check audit logs
INSERT INTO credit_checks (id, account_id, oauth_provider, external_id, wa_id, tenant_id, has_credit, credits_remaining, plan_name, denial_reason, context_agent_id, context_channel_id, context_request_id, created_at)
VALUES
    (
        uuid_generate_v4(),
        '550e8400-e29b-41d4-a716-446655440000',
        'oauth:google',
        'test-user-1@example.com',
        'wa-test-001',
        'tenant-acme',
        true,
        5000,
        'pro',
        NULL,
        'datum',
        'discord:12345',
        'req-check-001',
        NOW() - INTERVAL '5 days'
    ),
    (
        uuid_generate_v4(),
        '550e8400-e29b-41d4-a716-446655440002',
        'oauth:discord',
        'discord-user-123456',
        NULL,
        NULL,
        false,
        0,
        'free',
        'Insufficient credits',
        'datum',
        'discord:67890',
        'req-check-002',
        NOW() - INTERVAL '2 days'
    ),
    (
        uuid_generate_v4(),
        '550e8400-e29b-41d4-a716-446655440003',
        'oauth:google',
        'suspended-user@example.com',
        'wa-test-003',
        'tenant-acme',
        false,
        1000,
        'pro',
        'Account suspended',
        'datum',
        'web:session-456',
        'req-check-003',
        NOW() - INTERVAL '1 day'
    )
ON CONFLICT DO NOTHING;

-- Print summary
DO $$
DECLARE
    account_count INTEGER;
    credit_count INTEGER;
    charge_count INTEGER;
    check_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO account_count FROM accounts;
    SELECT COUNT(*) INTO credit_count FROM credits;
    SELECT COUNT(*) INTO charge_count FROM charges;
    SELECT COUNT(*) INTO check_count FROM credit_checks;

    RAISE NOTICE '==============================================';
    RAISE NOTICE 'Test Data Initialization Complete';
    RAISE NOTICE '==============================================';
    RAISE NOTICE 'Accounts:      %', account_count;
    RAISE NOTICE 'Credits:       %', credit_count;
    RAISE NOTICE 'Charges:       %', charge_count;
    RAISE NOTICE 'Credit Checks: %', check_count;
    RAISE NOTICE '==============================================';
END $$;
