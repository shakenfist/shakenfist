#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# bounded-test-run.sh -- run a test command under a memory bound.
#
# tox.ini sets OS_TEST_TIMEOUT, which bounds how long a single test may
# take. Nothing bounded how much a worker may allocate, so a leaking
# test passed and the worker carried the leak into every test scheduled
# after it. One reached 16.3 GB resident plus 5.9 GB of swap and held a
# workstation in direct reclaim for an hour (issue #4928).
#
# A per-run cgroup is the preferred mechanism: the kernel kills only
# this scope, no library allocation raises a confusing MemoryError, and
# it bounds swap as well as resident memory -- which matters, because
# the worse of the two episodes put about 31 GB into swap. It needs a
# user systemd session, which a CI runner does not have, so where the
# scope cannot be created fall back to RLIMIT_DATA. Since Linux 4.7
# that covers private anonymous mappings as well as the brk heap, so it
# tracks heap growth, and being a per-process limit it is inherited by
# every worker. RLIMIT_AS is deliberately not used: CPython and its
# native extensions reserve far more address space than they ever
# touch, so a bound loose enough not to misfire would not bite until
# the machine already hurt.
#
# Both defaults are per worker, because the legitimate footprint is: a
# worker of this suite peaks at a little over 200 MB and stestr runs one
# per CPU. The scope's bound is therefore that budget times the CPU
# count, which is also the difference between the two mechanisms --
# RLIMIT_DATA bounds each worker and says nothing about their sum, while
# the scope bounds the run as a whole. Capping the machine rather than
# the run (a limit on the session's cgroup) is an operator matter and
# not this repository's.
#
# Set SF_TEST_MEMORY_MAX (the scope's MemoryMax), SF_TEST_SWAP_MAX (its
# MemorySwapMax) or SF_TEST_HEAP_MAX_MB (the RLIMIT_DATA fallback, in
# megabytes) to override, or any of them to 'none' to go without.

set -u

workers="$(nproc 2>/dev/null || echo 4)"
memory_max="${SF_TEST_MEMORY_MAX:-$((workers * 768))M}"
swap_max="${SF_TEST_SWAP_MAX:-1G}"
heap_max_mb="${SF_TEST_HEAP_MAX_MB:-2048}"

if [ "$#" -lt 1 ]; then
    echo "usage: $0 <command> [argument ...]" 1>&2
    exit 2
fi

# The probe is the only honest test of whether a scope can be created:
# systemd-run may be installed with no session bus to talk to, and
# MemorySwapMax needs swap accounting enabled.
if [ "${memory_max}" != 'none' ] \
        && systemd-run --user --scope -q -p MemoryMax="${memory_max}" \
            -p MemorySwapMax="${swap_max}" -- true >/dev/null 2>&1; then
    echo "Bounding this run: MemoryMax=${memory_max} MemorySwapMax=${swap_max}"
    exec systemd-run --user --scope -q -p MemoryMax="${memory_max}" \
        -p MemorySwapMax="${swap_max}" -- "$@"
fi

if [ "${heap_max_mb}" = 'none' ]; then
    echo "Not bounding this run: SF_TEST_HEAP_MAX_MB=none"
else
    ulimit -d "$((heap_max_mb * 1024))" \
        || echo "Could not set RLIMIT_DATA to ${heap_max_mb}MB" 1>&2
    echo "Bounding this run: RLIMIT_DATA=$(ulimit -d) KiB per process"
fi

exec "$@"
