#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# ci-enum-widening-test.sh -- run the live MariaDB tests against a
# local MariaDB. The caller must have installed MariaDB first (see
# tools/ci-install-mariadb.sh), with the shakenfist database and user
# from tools/bootstrap-mariadb.sql.
#
# The ENUM widening tests simulate a database created before the newest
# member of each Python enum existed (the failure mode that broke
# namespace key writes when ObjectType.NAMESPACE_KEY shipped without a
# widening migration), then verify ensure_schema()'s reconciliation
# pass widens the ENUM columns and the new values become writable.
#
# It now runs every shakenfist/tests/test_mariadb_*_live.py module,
# not only that one, because standing up MariaDB is the expensive part
# and any test which needs a real server belongs behind the same
# setup. The script and its workflow job keep their ENUM widening
# names so the required status check does not change; rename both
# together if that stops being worth it.
#
# Usage:
#
#   bash tools/ci-enum-widening-test.sh <mariadb-password>
#
# Arguments:
#
#   $1   the MariaDB password for the shakenfist user (required)
#
# The tests are DESTRUCTIVE to the shakenfist database (tables are
# dropped during cleanup), which is fine on the ephemeral CI VM this
# script targets. Do not run against a real deployment.

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <mariadb-password>" >&2
    exit 1
fi

DB_PASSWORD="$1"

# The mariadb+mysqldb SQLAlchemy dialect (the one production uses, and
# the only one that supports MariaDB-specific types like INET4) needs
# the system python3-mysqldb bindings, hence --system-site-packages.
echo "Installing system MySQLdb bindings..."
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-mysqldb

echo "Building test venv..."
rm -rf /tmp/venv-enum-widening
python3 -mvenv --system-site-packages /tmp/venv-enum-widening
# shellcheck disable=SC1091
. /tmp/venv-enum-widening/bin/activate
pip3 install uv
uv pip install -e '.[test]'

export SF_MARIADB_TEST_DSN="mariadb+mysqldb://shakenfist:${DB_PASSWORD}@127.0.0.1:3306/shakenfist"

# The live capacity suite only sees ER_CHECKREAD regressions (a plain
# SELECT ahead of a guarded UPDATE, issue #3759) when the server runs
# with innodb_snapshot_isolation ON, the default from MariaDB 11.6.2.
# The suites report the regime but pass either way, because developers
# run them against 10.11 too. Here that would let a runner image
# rebuild or a distro move silently drop the coverage, so the job
# refuses to run against any other regime. On a server too old to know
# the variable the query errors, which fails the same way.
echo "Checking the server runs with innodb_snapshot_isolation ON..."
regime=$(mariadb -h 127.0.0.1 -u shakenfist -p"${DB_PASSWORD}" -N -B \
    -e 'SELECT VERSION(), @@GLOBAL.innodb_snapshot_isolation' 2>&1) || true
echo "Server regime: ${regime}"
if ! echo "${regime}" | grep -qE $'\t1$'; then
    echo "ERROR: innodb_snapshot_isolation is not ON on this server." >&2
    echo "This job exists to run the live suites under it; see the" >&2
    echo "schema_enum_widening comment in functional-tests.yml." >&2
    exit 1
fi

echo "Running live MariaDB tests..."
# --serial because these share one database and drop their tables on
# cleanup. The regex is anchored on the module name so a live test
# module added later is picked up without touching this script.
stestr run --serial 'shakenfist\.tests\.test_mariadb_.*_live\.' \
    | tee /tmp/live-mariadb-output

# Every test in these modules is @unittest.skipUnless(SF_MARIADB_TEST_DSN),
# so a broken export -- a typo, a rename of the environment variable, a
# shell change that drops it -- turns this job into a clean run of zero
# tests and a green tick, and the guard silently stops guarding.
if ! grep -qE '^ - Passed: [1-9]' /tmp/live-mariadb-output; then
    echo "ERROR: no live MariaDB tests actually ran." >&2
    echo "Is SF_MARIADB_TEST_DSN reaching the test process?" >&2
    exit 1
fi

echo "Live MariaDB tests passed."
