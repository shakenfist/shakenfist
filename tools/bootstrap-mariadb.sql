-- Shaken Fist BYO MariaDB bootstrap snippet.
--
-- Apply this against your MariaDB server to create the SF database,
-- a dedicated user, and the necessary grants. The snippet is
-- idempotent and safe to re-run.
--
-- The placeholder __REPLACE_ME__ is the password for the shakenfist
-- user. Replace it with your chosen password BEFORE applying. The
-- recommended pattern is sed-replace into a temporary file or pipe
-- through sed so the password never appears on a process listing:
--
--   sed 's/__REPLACE_ME__/your-password-here/' \
--       tools/bootstrap-mariadb.sql | mysql -u root
--
-- The database name, user name, and grant scope match Shaken Fist's
-- defaults; operators who want different names can edit the snippet
-- and provide the corresponding values to `getsf` when prompted.
--
-- After applying this snippet, run `sf-ctl ensure-mariadb-schema`
-- to create the SF tables and apply any pending schema migrations.

-- We intentionally do not pin a COLLATE here: the database inherits
-- the server's default collation for utf8mb4. Pinning a non-default
-- collation makes JOINs that compare a table column (which carries
-- the column's collation) against CAST() output (which carries the
-- server's default collation) raise "Illegal mix of collations"
-- (MariaDB error 1267) -- which is exactly what bit the
-- coalescible-op join in cluster CI. Shaken Fist's comparisons are
-- over UUIDs, integers, and ASCII keys, where utf8mb4_general_ci
-- and utf8mb4_unicode_ci are interchangeable, so letting the server
-- pick avoids the mismatch.
CREATE DATABASE IF NOT EXISTS `shakenfist`
    CHARACTER SET utf8mb4;

CREATE USER IF NOT EXISTS 'shakenfist'@'%'
    IDENTIFIED BY '__REPLACE_ME__';

GRANT ALL ON `shakenfist`.* TO 'shakenfist'@'%';

FLUSH PRIVILEGES;
