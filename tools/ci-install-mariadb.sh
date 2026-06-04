#!/bin/bash
# Copyright 2019 Michael Still and contributors
#
# ci-install-mariadb.sh -- apt-install MariaDB, apply the SF bootstrap
# snippet, and optionally drop in the recommended tuning. Runs on the
# target box (not over SSH from a runner); the caller is responsible
# for putting the SQL snippet and tuning file at the paths it passes
# in.
#
# Usage:
#
#   sudo bash tools/ci-install-mariadb.sh \
#       /path/to/bootstrap-mariadb.sql \
#       /path/to/mariadb-tuning.cnf \
#       my-mariadb-password
#
# Arguments:
#
#   $1   path to tools/bootstrap-mariadb.sql (required)
#   $2   path to examples/mariadb-tuning.cnf, or '' to skip (required
#        arg, may be empty)
#   $3   the MariaDB password for the shakenfist user (required)
#
# The script is used by CI to install a BYO-style MariaDB on the
# functional-test VM, and by developers spinning up single-box dev
# deploys. CI passes a fixed test password; developers pass whatever
# they choose. The script writes a bind-all drop-in so MariaDB listens
# on all interfaces (required for multi-node CI shapes; harmless on
# single-node).

set -euo pipefail

if [ $# -ne 3 ]; then
    echo "Usage: $0 <bootstrap.sql> <tuning.cnf|''> <password>" >&2
    exit 1
fi

SQL_PATH="$1"
TUNING_PATH="$2"
DB_PASSWORD="$3"

if [ ! -f "${SQL_PATH}" ]; then
    echo "Bootstrap SQL not found at: ${SQL_PATH}" >&2
    exit 1
fi
if [ -n "${TUNING_PATH}" ] && [ ! -f "${TUNING_PATH}" ]; then
    echo "Tuning file not found at: ${TUNING_PATH}" >&2
    exit 1
fi

echo "Installing mariadb-server..."
# Retry-on-apt-lock loop: cloud-init or unattended-upgrades may hold
# the lock briefly during VM startup. Bound the loop so we eventually
# give up rather than spin forever.
attempt=0
max_attempts=60
until apt-get update -y && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y mariadb-server; do
    attempt=$((attempt + 1))
    if [ "${attempt}" -ge "${max_attempts}" ]; then
        echo "apt-get install failed after ${max_attempts} retries" >&2
        exit 1
    fi
    echo "apt locked or transient failure, retrying" \
        "(${attempt}/${max_attempts})..."
    sleep 5
done

echo "Waiting for mariadb to be active..."
attempt=0
until systemctl is-active --quiet mariadb; do
    attempt=$((attempt + 1))
    if [ "${attempt}" -ge 30 ]; then
        echo "mariadb did not become active within 30 attempts" >&2
        systemctl status mariadb || true
        exit 1
    fi
    sleep 2
done

echo "Applying SF bootstrap snippet..."
sed "s/__REPLACE_ME__/${DB_PASSWORD}/" "${SQL_PATH}" | mysql -u root

echo "Writing bind-all drop-in for multi-node access..."
cat > /etc/mysql/mariadb.conf.d/99-bind-all.cnf <<'EOF'
[mysqld]
bind-address = 0.0.0.0
EOF

if [ -n "${TUNING_PATH}" ]; then
    echo "Installing SF tuning drop-in..."
    cp "${TUNING_PATH}" /etc/mysql/mariadb.conf.d/
else
    echo "Skipping tuning install (empty path)"
fi

echo "Restarting MariaDB to pick up bind-all and tuning..."
systemctl restart mariadb
attempt=0
until systemctl is-active --quiet mariadb; do
    attempt=$((attempt + 1))
    if [ "${attempt}" -ge 30 ]; then
        echo "mariadb did not restart cleanly" >&2
        systemctl status mariadb || true
        exit 1
    fi
    sleep 2
done

echo "MariaDB ready."
