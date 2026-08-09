BEGIN;

-- Dev-time init script: dropped in reverse dependency order so
-- this file can be re-run after every edit.
DROP TABLE IF EXISTS policy_files CASCADE;
DROP TABLE IF EXISTS user_tenants CASCADE;
DROP TABLE IF EXISTS users        CASCADE;
DROP TABLE IF EXISTS tenants      CASCADE;
DROP TABLE IF EXISTS customers    CASCADE;

-- A customer is an organisation, e.g. "Globex Inc".
CREATE TABLE customers (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    folder_name TEXT NOT NULL UNIQUE, -- folder name in the Git repo
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- character constraints mean a path-unsafe value cannot exist in the
    -- database at all, no matter what the application does.
    CONSTRAINT customers_folder_name_format
        CHECK (folder_name ~ '^[a-z0-9][a-z0-9-]*$' AND char_length(folder_name) <= 64)
);

-- A tenant is an environment belonging to a customer, e.g. "Production".
CREATE TABLE tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES customers (id) ON DELETE RESTRICT,
    name        TEXT NOT NULL,
    folder_name TEXT NOT NULL, -- folder name in the Git repo
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Two customers can both have a "production", one customer may not.
    UNIQUE (customer_id, folder_name),

    CONSTRAINT tenants_folder_name_format
        CHECK (folder_name ~ '^[a-z0-9][a-z0-9-]*$' AND char_length(folder_name) <= 64)
);

-- A user belongs to one customer.
CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES customers (id) ON DELETE RESTRICT,
    email       TEXT NOT NULL UNIQUE,
    api_token   TEXT NOT NULL UNIQUE  -- how the user logs in
);

-- Which tenants a user is allowed to see. One user can have several.
CREATE TABLE user_tenants (
    user_id   UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,

    PRIMARY KEY (user_id, tenant_id) -- a user can only have one entry per tenant
);

-- One row per uploaded Cedar file.
CREATE TABLE policy_files (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID        NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    filename    TEXT        NOT NULL,
    git_path    TEXT        NOT NULL,   -- full path inside the Git repo
    commit_sha  TEXT        NOT NULL,   -- the commit this version was saved in
    size_bytes  INTEGER     NOT NULL,
    uploaded_by UUID        NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ, -- NULL means the file is still there

    -- Second line of defence behind the application's filename validation.
    -- Excluding '/' is what makes path traversal unrepresentable.
    CONSTRAINT policy_files_filename_format
        CHECK (filename ~ '^[A-Za-z0-9][A-Za-z0-9._-]*\.cedar$'
               AND char_length(filename) <= 255),

    CONSTRAINT policy_files_size_non_negative
        CHECK (size_bytes >= 0)
);

-- A tenant can't have two files with the same name at the same time.
-- The "WHERE deleted_at IS NULL" part only counts files that haven't been
-- deleted, so you can delete a file and upload one with the same name later.
CREATE UNIQUE INDEX policy_files_one_live_name_per_tenant
    ON policy_files (tenant_id, filename)
    WHERE deleted_at IS NULL;


-- Almost every query filters by tenant_id, so give Postgres an index for it.
CREATE INDEX policy_files_tenant_id_idx ON policy_files (tenant_id);


COMMIT;
