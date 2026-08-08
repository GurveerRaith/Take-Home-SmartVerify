-- Test data. There is no signup flow, so users are created here.

-- The IDs and tokens are written out by hand instead of being generated, so
-- that the README, the tests and any curl commands can all use the same
-- values.

BEGIN;

INSERT INTO customers (id, name, folder_name, created_at) VALUES
    ('11111111-1111-1111-1111-111111111111', 'Globex', 'globex', NOW()),
    ('22222222-2222-2222-2222-222222222222', 'Initech',    'initech', NOW());

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

COMMIT;