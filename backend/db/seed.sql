-- Seed data. There is no signup flow, so users are created here.
-- The IDs and tokens are written out by hand instead of being generated.
-- This is so that the README, the tests and any curl commands can all use the same values.

BEGIN;


INSERT INTO customers (id, name, folder_name) VALUES
    ('11111111-1111-1111-1111-111111111111', 'Globex',  'globex'),
    ('22222222-2222-2222-2222-222222222222', 'Initech', 'initech');


INSERT INTO tenants (id, customer_id, name, folder_name) VALUES
    ('aaaaaaaa-0000-0000-0000-000000000001',
     '11111111-1111-1111-1111-111111111111', 'Production', 'production'),
    ('aaaaaaaa-0000-0000-0000-000000000002',
     '11111111-1111-1111-1111-111111111111', 'Staging',    'staging'),
    ('bbbbbbbb-0000-0000-0000-000000000001',
     '22222222-2222-2222-2222-222222222222', 'Production',       'production');


INSERT INTO users (id, customer_id, email, api_token) VALUES
    ('cccccccc-0000-0000-0000-000000000001',
     '11111111-1111-1111-1111-111111111111',
     'alice@globex.example',   'alice_token'),

    ('cccccccc-0000-0000-0000-000000000002',
     '11111111-1111-1111-1111-111111111111',
     'bob@globex.example',     'bob_token'),

    ('cccccccc-0000-0000-0000-000000000003',
     '22222222-2222-2222-2222-222222222222',
     'carol@initech.example', 'carol_token');


-- Alice: Globex production and staging.
-- Bob: Globex production only.
-- Carol: Initech production only.
INSERT INTO user_tenants (user_id, tenant_id) VALUES
    ('cccccccc-0000-0000-0000-000000000001',
     'aaaaaaaa-0000-0000-0000-000000000001'),
    ('cccccccc-0000-0000-0000-000000000001',
     'aaaaaaaa-0000-0000-0000-000000000002'),

    ('cccccccc-0000-0000-0000-000000000002',
     'aaaaaaaa-0000-0000-0000-000000000001'),

    ('cccccccc-0000-0000-0000-000000000003',
     'bbbbbbbb-0000-0000-0000-000000000001');


-- A user must never be granted a tenant belonging to a different customer.
-- The schema does not enforce this: expressing it structurally would need a
-- composite foreign key on user_tenants, which was considered and rejected
-- (see DESIGN.md). This guard replaces it.
--
-- It must stay INSIDE the transaction, above the COMMIT below. Run after the
-- COMMIT it would still report the problem, but the bad grant would already
-- be live in the database. Here, a violation rolls the entire seed back.
DO $$
DECLARE
    bad_count integer;
BEGIN
    SELECT count(*) INTO bad_count
    FROM user_tenants ut
    JOIN users u ON u.id = ut.user_id
    JOIN tenants t ON t.id = ut.tenant_id
    WHERE u.customer_id <> t.customer_id;

    IF bad_count > 0 THEN
        RAISE EXCEPTION 'seed data contains % cross-customer grant(s)', bad_count;
    END IF;
END $$;

COMMIT;