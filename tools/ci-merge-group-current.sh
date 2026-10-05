#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# ci-merge-group-current.sh -- cancel this merge group run if GitHub has
# already rebuilt its queue entry onto a newer base.
#
# The queue's jobs share concurrency groups keyed on the merge group's
# base_ref, so that a rebuilt queue entry cancels the run it superseded
# rather than both running to completion on the shared under-cloud. But
# cancel-in-progress cancels whichever run entered the group *first*, and
# which run that is gets decided by a race between the two runs' Check paths
# jobs, not by which base is live. When the superseded run won that race it
# cancelled the live one, and the pull request was ejected from the queue
# with nothing tested (issue #3998).
#
# So a superseded run removes itself before its jobs enter those groups.
# The develop ruleset sets max_entries_to_build: 1, so a live merge group is
# always built directly on top of the base branch's head, and a run whose
# base_sha is no longer that head has been superseded -- whether by the
# queue merging the entry ahead of it, or by a push to the base branch from
# outside the queue. This runs as the last step of Check paths, which is the
# job every queue job needs, so the gap between this check and the jobs
# entering their groups is seconds. A rebuild landing inside that gap still
# resolves correctly: the replacement run is only created after the push,
# and its own jobs cannot enter the groups until its Check paths has run, so
# they enter last and cancel ours.
#
# The two ways this can be wrong are not equally bad, and the script leans
# accordingly. Calling a live run superseded cancels it, which is the bug
# this exists to fix, so when the base branch's head cannot be read the run
# is treated as current and carries on. Calling a superseded run current
# only leaves things as they were before this check existed. Once a run has
# been judged superseded, though, it does not carry on if its cancellation
# fails: it exits non-zero, which skips every queue job and fails Can merge
# on a commit the queue has already abandoned.
#
# Expects BASE_REF, BASE_SHA and RUN_ID in the environment (the merge_group
# event's base_ref and base_sha, and github.run_id), GH_TOKEN with
# actions: write, and to run inside a checkout whose origin remote can be
# read. CANCEL_WAIT_SECONDS bounds how long it waits for its own
# cancellation to land, and exists for the tests.

set -uo pipefail

: "${BASE_REF:?BASE_REF must be set}"
: "${BASE_SHA:?BASE_SHA must be set}"
: "${RUN_ID:?RUN_ID must be set}"
wait_seconds="${CANCEL_WAIT_SECONDS:-120}"

if ! head=$(git ls-remote --exit-code origin "${BASE_REF}" | cut -f1) \
        || [ -z "${head}" ]; then
    echo "::warning::Could not read ${BASE_REF} from origin, so cannot tell" \
         "whether this merge group is current. Assuming it is."
    exit 0
fi

if [ "${head}" == "${BASE_SHA}" ]; then
    echo "This merge group is built on ${BASE_SHA}, the head of ${BASE_REF}."
    exit 0
fi

echo "::notice::This merge group is built on ${BASE_SHA}, but ${BASE_REF} is" \
     "now ${head}. GitHub has rebuilt the queue entry on the new base, so" \
     "this run is superseded and is cancelling itself."

if ! gh run cancel "${RUN_ID}"; then
    echo "::error::Could not cancel superseded run ${RUN_ID}. Failing instead" \
         "so that no queue job starts."
    exit 1
fi

# The cancellation is asynchronous. Wait for it to stop this step, so that
# the run concludes cancelled rather than failed and merge failure triage
# does not pick it up.
waited=0
while [ "${waited}" -lt "${wait_seconds}" ]; do
    sleep 5
    waited=$((waited + 5))
done

echo "::error::Run ${RUN_ID} was still running ${wait_seconds} seconds after" \
     "cancelling itself. Failing instead so that no queue job starts."
exit 1
