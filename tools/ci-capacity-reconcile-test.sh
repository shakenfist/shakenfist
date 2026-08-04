#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# ci-capacity-reconcile-test.sh -- run the live scheduler capacity
# reconciler tests against a local MariaDB. The caller must have
# installed MariaDB first (see tools/ci-install-mariadb.sh), with the
# shakenfist database and user from tools/bootstrap-mariadb.sql.
#
# The reconciler's other tests run against a mocked connection and
# assert on compiled statement text, which cannot exercise the parts
# most likely to break: the JSON_TABLE disk aggregation against
# malformed payloads, the REPLACE(dashed, '-', '') joins landing on the
# instances CHAR(32) primary key (comparing the two uuid forms directly
# silently matches nothing rather than erroring), the is_hypervisor and
# node-state filters against real nullable columns, and both
# ON DUPLICATE KEY UPDATE upserts. These tests run the real SQL against
# a real server and assert the hand-computed values recorded in
# docs/plans/PLAN-scheduler-reservations-phase-02-capacity-tables.md.
#
# Usage:
#
#   bash tools/ci-capacity-reconcile-test.sh <mariadb-password>
#
# Arguments:
#
#   $1   the MariaDB password for the shakenfist user (required)
#
# The tests are DESTRUCTIVE to the shakenfist database (tables are
# dropped during setup and cleanup), which is fine on the ephemeral CI
# VM this script targets. Do not run against a real deployment.

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
rm -rf /tmp/venv-capacity-reconcile
python3 -mvenv --system-site-packages /tmp/venv-capacity-reconcile
# shellcheck disable=SC1091
. /tmp/venv-capacity-reconcile/bin/activate
pip3 install uv
uv pip install -e '.[test]'

export SF_MARIADB_TEST_DSN="mariadb+mysqldb://shakenfist:${DB_PASSWORD}@127.0.0.1:3306/shakenfist"

# Serial because every test in the module shares one database and drops
# its tables during cleanup.
echo "Running live capacity reconciler tests..."
stestr run --serial test_mariadb_capacity_reconcile_live

echo "Live capacity reconciler tests passed."
