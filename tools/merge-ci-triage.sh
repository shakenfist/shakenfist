#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# Triage a failed merge queue CI run with Claude Code.
#
#   merge-ci-triage.sh <run id>
#
# Reproduces, unattended, what a maintainer does by hand after a merge queue
# ejection: work out whether the failure was caused by the pull request in the
# merge group or by something systemic, record a systemic failure against its
# tracking issue, and say whether the pull request should be re-queued as-is or
# fixed first. docs/developer_guide/ci.md describes the procedure.
#
# Environment:
#   REPO            owner/repo to triage in (default: GITHUB_REPOSITORY)
#   OUTPUT_DIR      where evidence, the prompt and triage.json are written
#                   (default: a mktemp directory, printed on exit)
#   MODELS          comma separated models to try, in preference order
#   MAX_TURNS       turn cap for the triage run
#   DRY_RUN         "true" to classify without writing anything to GitHub
#   TRIAGE_RUN_URL  URL of the run doing the triage, recorded in the verdict
#   GH_TOKEN        token for gh, as usual. Needs actions:read to read the
#                   failed run at all, plus issues:write and
#                   pull-requests:write to publish anything.
#
# The deliverable is $OUTPUT_DIR/triage.json, a document matching
# tools/merge-triage-schema.json. It is written whatever happens -- a model
# which produces nothing usable, or a run which cannot be read at all, yields
# a verdict of "unknown" rather than no file -- because the private-ci
# conductor consumes these to track which merge failures have been triaged and
# which of them blamed the pull request. A missing document is
# indistinguishable from a triage that never ran; an "unknown" one is not.
# That promise is kept by an EXIT trap over an envelope written before
# anything can fail, so it holds for the paths which fall over early rather
# than only for the ones which reach a model.
#
# The one exception is a run which is not a failed merge_group run at all.
# Nothing was triaged, so there is nothing to publish a verdict about.
#
# Three things the model reports are checked rather than believed, on the same
# argument the issue-fix workflow makes when it re-runs the test suite the
# model claims to have run:
#
# - The envelope (repository, run id, pull request number) is built by
#   tools/merge-triage.py from what GitHub said, and overwrites whatever the
#   model put in those fields.
# - A tracking issue the model says it commented on has to exist, and the
#   issue or one of its comments has to carry this run's URL. A verdict which
#   cites an issue that does not mention the failure is downgraded to "no
#   issue", because the conductor treats a cited issue as evidence the
#   occurrence was recorded. The comments are paged through in full: a
#   long-lived flake issue accumulates one comment per occurrence, and reading
#   only the first page would false-drop the comment just written.
# - Evidence that could not be gathered is recorded as such. A model handed an
#   empty log and asked for a verdict will still produce one, and the prompt's
#   own heuristics push an evidence-free run towards "systemic, re-queue" --
#   which is precisely the confidently wrong document the rest of this design
#   works to prevent. With nothing readable at all, no model is run.
#
# The pull request comment carries the same JSON in a collapsed details
# section, mirroring the automated reviewer, so the verdict can be recovered
# from the thread as well as from the run's artifact.

set -uo pipefail

RUN_ID="${1:-}"
if [ -z "${RUN_ID}" ]; then
    echo "usage: merge-ci-triage.sh <run id>" >&2
    exit 2
fi

# Before anything else, because this value ends up in the envelope -- the part
# of the document the design says nothing untrusted can influence -- and the
# workflow accepts it as a string from workflow_dispatch.
if ! [[ "${RUN_ID}" =~ ^[0-9]+$ ]]; then
    echo "merge-ci-triage: run id must be numeric, got '${RUN_ID}'" >&2
    exit 2
fi

REPO="${REPO:-${GITHUB_REPOSITORY:-}}"
if [ -z "${REPO}" ]; then
    echo "merge-ci-triage: REPO or GITHUB_REPOSITORY must be set" >&2
    exit 2
fi

OUTPUT_DIR="${OUTPUT_DIR:-$(mktemp -d)}"
MODELS="${MODELS:-claude-fable-5,claude-opus-5}"
MAX_TURNS="${MAX_TURNS:-60}"
DRY_RUN="${DRY_RUN:-false}"
TRIAGE_RUN_URL="${TRIAGE_RUN_URL:-}"

TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "${OUTPUT_DIR}"
echo "merge-ci-triage: working in ${OUTPUT_DIR}"

TRIAGE_JSON="${OUTPUT_DIR}/triage.json"
ENVELOPE_JSON="${OUTPUT_DIR}/envelope.json"

# The minimum envelope, written before the first thing that can fail. It is
# replaced with the full one as soon as GitHub has been asked about the run;
# until then it is what lets a failure still publish a document.
cat > "${ENVELOPE_JSON}" <<ENVELOPE_EOF
{
  "repository": "${REPO}",
  "run_id": ${RUN_ID},
  "run_url": "https://github.com/${REPO}/actions/runs/${RUN_ID}",
  "triage_run_url": $([ -n "${TRIAGE_RUN_URL}" ] && echo "\"${TRIAGE_RUN_URL}\"" || echo null)
}
ENVELOPE_EOF

publish_document=true
failure_reason='Triage exited before it reached a verdict.'

publish_fallback_on_exit() {
    if [ "${publish_document}" != "true" ]; then
        return
    fi
    if [ -f "${TRIAGE_JSON}" ]; then
        return
    fi
    python3 "${TOOLS_DIR}/merge-triage.py" fallback \
        "${ENVELOPE_JSON}" "${TRIAGE_JSON}" "${failure_reason}" \
        || echo "merge-ci-triage: could not write a fallback document" >&2
    echo "merge-ci-triage: no verdict was reached: ${failure_reason}" >&2
}
trap publish_fallback_on_exit EXIT

# ---------------------------------------------------------------------------
# What are we looking at?
# ---------------------------------------------------------------------------

if ! gh run view "${RUN_ID}" --repo "${REPO}" \
        --json databaseId,event,conclusion,headBranch,headSha,url,attempt,workflowName,createdAt \
        > "${OUTPUT_DIR}/run.json"; then
    failure_reason="The failed run ${RUN_ID} could not be read from ${REPO}. The token needs actions:read."
    echo "merge-ci-triage: ${failure_reason}" >&2
    exit 1
fi

run_event=$(jq -r '.event // ""' "${OUTPUT_DIR}/run.json")
run_conclusion=$(jq -r '.conclusion // ""' "${OUTPUT_DIR}/run.json")
head_branch=$(jq -r '.headBranch // ""' "${OUTPUT_DIR}/run.json")
run_url=$(jq -r '.url // ""' "${OUTPUT_DIR}/run.json")
run_attempt=$(jq -r '.attempt // 1' "${OUTPUT_DIR}/run.json")

echo "merge-ci-triage: ${REPO} run ${RUN_ID} (${run_event}, ${run_conclusion}) on ${head_branch}"

# The workflow filters on these too, but this script is also run by hand
# against a run id, and triaging a green run or a pull request run would
# produce a confident verdict about nothing. Nothing was triaged, so this is
# the one exit which publishes no document.
if [ "${run_event}" != "merge_group" ] || [ "${run_conclusion}" != "failure" ]; then
    echo "merge-ci-triage: not a failed merge_group run, nothing to triage" >&2
    publish_document=false
    exit 0
fi

if ! python3 "${TOOLS_DIR}/merge-triage.py" envelope \
        "${REPO}" "${OUTPUT_DIR}/run.json" "${ENVELOPE_JSON}" "${TRIAGE_RUN_URL}"; then
    failure_reason='The run metadata could not be turned into a verdict envelope.'
    exit 1
fi

base_branch=$(jq -r '.base_branch // ""' "${ENVELOPE_JSON}")
pr_number=$(jq -r '.pull_request // ""' "${ENVELOPE_JSON}")

# ---------------------------------------------------------------------------
# Gather the evidence
# ---------------------------------------------------------------------------

# Anything that could not be read is recorded and travels with the verdict.
# Silence here is how a model ends up classifying a failure it was never shown.
gather_notes=()

if ! gh run view "${RUN_ID}" --repo "${REPO}" --json jobs \
        --jq '[.jobs[] | select(.conclusion == "failure") |
               {job: .name, url: .url,
                failed_steps: [.steps[] | select(.conclusion == "failure") | .name]}]' \
        > "${OUTPUT_DIR}/failed-jobs.json" 2>/dev/null; then
    echo '[]' > "${OUTPUT_DIR}/failed-jobs.json"
    gather_notes+=('Triage could not read the list of failed jobs for this run.')
fi

# --log-failed is only the failed steps, which is the part worth reading, but
# a cluster build can still emit megabytes of it. The cap is on bytes rather
# than lines because one wrapped ansible line can be thousands of characters.
# The exit status of this pipeline is deliberately ignored: head closes the
# pipe once it has its bytes, which kills gh with SIGPIPE on any log longer
# than the cap -- that is the successful case, not a failure. Whether anything
# arrived is the question that matters, and the file answers it.
gh run view "${RUN_ID}" --repo "${REPO}" --log-failed 2>/dev/null \
    | head -c 120000 > "${OUTPUT_DIR}/failed-logs.txt" || true
if [ ! -s "${OUTPUT_DIR}/failed-logs.txt" ]; then
    gather_notes+=('Triage could not read the logs of the failed steps; they may have aged out of retention.')
fi

# Sibling merge groups, for the "is this happening to everybody?" question
# which is what separates systemic from PR-caused more reliably than the log
# does.
if ! gh run list --repo "${REPO}" --event merge_group --limit 20 \
        --json databaseId,conclusion,headBranch,createdAt,url \
        > "${OUTPUT_DIR}/sibling-runs.json" 2>/dev/null; then
    echo '[]' > "${OUTPUT_DIR}/sibling-runs.json"
    gather_notes+=('Triage could not read other recent merge group runs, so no correlation was possible.')
fi

echo '{}' > "${OUTPUT_DIR}/pr.json"
if [ -n "${pr_number}" ]; then
    if ! gh pr view "${pr_number}" --repo "${REPO}" --json number,title,author,files,body \
            > "${OUTPUT_DIR}/pr.json" 2>/dev/null; then
        echo '{}' > "${OUTPUT_DIR}/pr.json"
        gather_notes+=("Triage could not read pull request #${pr_number}, so the diff was not considered.")
    fi
else
    gather_notes+=("No pull request number could be parsed out of '${head_branch}'.")
fi

failed_job_count=$(jq 'length' "${OUTPUT_DIR}/failed-jobs.json" 2>/dev/null || echo 0)

# ---------------------------------------------------------------------------
# Classify
# ---------------------------------------------------------------------------

if [ "${failed_job_count}" -eq 0 ] && [ ! -s "${OUTPUT_DIR}/failed-logs.txt" ]; then
    # No jobs and no logs is not a hard case for a model, it is an absent one.
    # Asking anyway buys a confident verdict drawn entirely from the prompt's
    # own heuristics.
    echo "merge-ci-triage: nothing about the failure could be read, not running a model" >&2
    python3 "${TOOLS_DIR}/merge-triage.py" fallback \
        "${ENVELOPE_JSON}" "${TRIAGE_JSON}" \
        'Neither the failed jobs nor their logs could be read, so there was nothing to classify.'
else
    if [ "${DRY_RUN}" = "true" ]; then
        github_writes="DRY RUN: do not write anything to GitHub. Do not comment on
          or create any issue. Report in the JSON what you would have done,
          with tracking_issue_action set to \"none\"."
    else
        github_writes="You may comment on an existing issue, or create a new one,
          using gh. Do not comment on the pull request -- this workflow posts
          the verdict there itself. Do not push code, do not modify the pull
          request, and do not close anything."
    fi

    cat > "${OUTPUT_DIR}/prompt.txt" <<PROMPT_EOF
You are triaging a failed merge queue CI run for ${REPO}. This is a
non-interactive, single-shot run: everything you need to do must happen in this
turn, and nothing is waiting to ask you a question afterwards.

The failure:

- Run: ${run_url} (id ${RUN_ID}, attempt ${run_attempt})
- Merge group ref: ${head_branch}
- Pull request in the merge group: ${pr_number:-unknown}
- Base branch: ${base_branch:-unknown}

Your job, in order:

1. Work out what actually failed. The first failing step is the diagnosis;
   later failures are usually cascade. The failed jobs and steps, and the logs
   of the failed steps, are given below. You have gh and a checkout of the
   default branch, so you can read more with:
     gh run view ${RUN_ID} --repo ${REPO} --log-failed
     gh pr diff ${pr_number:-N} --repo ${REPO}
     gh run list --repo ${REPO} --event merge_group --limit 20

   Anything listed under "What could not be read" below is missing, not
   uninteresting. Say so in your evidence and lower your confidence
   accordingly rather than filling the gap with a plausible story.

2. Classify the failure as pr_caused, systemic, or ambiguous.

   Signals the pull request caused it: the failing test exercises code the
   diff changed; the failure looks deterministic (an assertion about behaviour
   the diff altered, a lint or schema error in a changed file); the same job
   passes on recent runs without this pull request.

   Signals it is systemic: the failure is in code or infrastructure the diff
   did not touch; infrastructure symptoms such as timeouts, connection
   refused, no SSH prompt, capacity errors, or cluster provisioning failing
   before tests run; the same signature in other recent merge_group runs; the
   pull request already passed the identical suite in its pull_request run.

   In this repository specifically: merge groups launch several nested test
   clusters at once, so merge CI is far more sensitive to under-cloud capacity
   than pull request CI, and historically most merge failures have been
   environmental rather than code regressions. A first failing step in cluster
   provisioning is almost always infrastructure. If it is a functional test,
   check the known flake issues before concluding it is a new bug.

   If it is genuinely ambiguous, say so. Do not manufacture confidence.

3. For a systemic failure, record the occurrence. Search open issues for the
   failure signature first, trying several phrasings, and check recently
   closed ones too -- most merge CI failures here are recurrences, not novel:
     gh issue list --repo ${REPO} --search 'TEST_NAME' --state open
     gh issue list --repo ${REPO} --search '"key error phrase"' --state all --limit 10

   If a tracking issue exists, comment on it recording this occurrence: the
   date, the run URL ${run_url}, which pull request hit it, the failing job and
   step, a few lines of log showing it is the same signature, and anything new
   this occurrence adds. Read the recent comments first and do not double-post
   an occurrence which is already recorded.

   If nothing tracks it, create an issue. The title must be the failure
   signature, specific enough that the search above finds it next time. The
   body needs the run URL, the pull request, the first failing step, a log
   excerpt, and why you believe it is systemic. Note that it was found during
   automated merge CI triage. Add the label automated-fix-attempted if a fix is
   already in flight or the issue needs human design work rather than a
   same-day patch, since the issue-fix workflow skips labelled issues.

   Your comment or issue body MUST contain the run URL ${run_url} verbatim. It
   is checked: a cited issue which does not reference this run is dropped from
   the verdict, because a consumer reads a cited issue as proof the occurrence
   was recorded.

   ${github_writes}

4. Emit your verdict as a single JSON object in a fenced json block, as the
   last thing you output. Nothing after it. The fields:

\`\`\`json
{
  "verdict": "pr_caused | systemic | ambiguous",
  "confidence": "high | medium | low",
  "summary": "One paragraph: what failed, and why the verdict is what it is.",
  "failing_job": "name of the first failing job",
  "failing_step": "name of the first failing step",
  "failure_signature": "short stable string for grouping recurrences, e.g. a test name or key error phrase",
  "recommendation": "requeue | fix_first | investigate",
  "tracking_issue": 1234,
  "tracking_issue_action": "commented | created | none",
  "evidence": ["short factual observations the verdict rests on"]
}
\`\`\`

  - tracking_issue is the issue number you commented on or created, or null,
    and tracking_issue_action says which. Report "none" if you wrote nothing:
    an unbacked claim is dropped, so it buys you nothing.
  - recommendation is requeue for a systemic failure the pull request cannot
    fix, fix_first when the pull request needs a change, investigate when a
    human has to decide.
  - Do not put an @ in front of any username anywhere in your output, and do
    not write "fixes", "closes" or "resolves" before an issue number. Both do
    something irreversible when this is posted.

# What could not be read

$(if [ ${#gather_notes[@]} -eq 0 ]; then
    echo 'Everything listed below was gathered successfully.'
else
    printf -- '- %s\n' "${gather_notes[@]}"
fi)

# Failed jobs and steps

$(cat "${OUTPUT_DIR}/failed-jobs.json")

# Recent merge_group runs on this repository

$(jq -r '.[] | "- \(.createdAt) \(.conclusion // "in progress") \(.headBranch) \(.url)"' \
    "${OUTPUT_DIR}/sibling-runs.json" 2>/dev/null || true)

# The pull request in the merge group

$(jq -r 'if .number then "#\(.number) \(.title) by \(.author.login // "unknown")\n\nFiles changed:\n" +
         ([.files[]? | "- \(.path) (+\(.additions)/-\(.deletions))"] | join("\n")) else "unknown" end' \
    "${OUTPUT_DIR}/pr.json" 2>/dev/null || true)

# Logs of the failed steps (truncated)

$(cat "${OUTPUT_DIR}/failed-logs.txt")
PROMPT_EOF

    echo "merge-ci-triage: running triage with models ${MODELS}"
    "${TOOLS_DIR}/claude-model-fallback.sh" \
        --models "${MODELS}" \
        -- "$(cat "${OUTPUT_DIR}/prompt.txt")" \
        --dangerously-skip-permissions \
        --max-turns "${MAX_TURNS}" \
        --output-format text \
        2>&1 | tee "${OUTPUT_DIR}/response.txt" \
        || true

    if ! python3 "${TOOLS_DIR}/merge-triage.py" extract \
            "${OUTPUT_DIR}/response.txt" "${ENVELOPE_JSON}" "${TRIAGE_JSON}"; then
        echo "merge-ci-triage: triage produced no verdict, publishing the fallback document"
    fi

    # Whatever could not be gathered travels with the verdict, so a reader is
    # never left to assume the model saw everything.
    if [ ${#gather_notes[@]} -gt 0 ]; then
        printf '%s\n' "${gather_notes[@]}" | jq -R . | jq -s . \
            > "${OUTPUT_DIR}/gather-notes.json"
        jq --slurpfile notes "${OUTPUT_DIR}/gather-notes.json" \
            '.evidence += $notes[0]' "${TRIAGE_JSON}" \
            > "${TRIAGE_JSON}.notes" && mv "${TRIAGE_JSON}.notes" "${TRIAGE_JSON}"
    fi
fi

# ---------------------------------------------------------------------------
# Check what the model said it did
# ---------------------------------------------------------------------------

if [ "${DRY_RUN}" = "true" ]; then
    # Nothing was written to GitHub, whatever the model believes, and the
    # document must not claim an occurrence was recorded. Forced here rather
    # than left to the prompt: this is the field a consumer is told to trust.
    jq '.tracking_issue_action = "none" |
        .evidence += ["This was a dry run: nothing was written to GitHub."]' \
        "${TRIAGE_JSON}" > "${TRIAGE_JSON}.dry" \
        && mv "${TRIAGE_JSON}.dry" "${TRIAGE_JSON}"
fi

# Run whenever an issue is cited, not only when the document claims a write:
# a dry run has already been forced to "none" above, and gating the check on
# the action would mean the verification path was never exercised except in
# production. What differs in a dry run is the consequence -- the note is
# recorded, but the citation is left in place, because "the issue it would
# have used" is the useful half of a dry run's output.
tracking_issue=$(jq -r '.tracking_issue // empty' "${TRIAGE_JSON}")
if [ -n "${tracking_issue}" ]; then
    # The issue body plus every comment, paged in full. A tracking issue for a
    # recurring flake carries one comment per occurrence, and a single page of
    # them would stop short of the comment just written.
    issue_text=$(
        {
            gh issue view "${tracking_issue}" --repo "${REPO}" --json body --jq '.body'
            gh api --paginate "repos/${REPO}/issues/${tracking_issue}/comments" \
                --jq '.[].body'
        } 2>/dev/null)

    drop_reason=""
    if [ -z "${issue_text}" ]; then
        drop_reason="Triage cited issue #${tracking_issue}, which could not be read in ${REPO}."
    elif ! echo "${issue_text}" | grep -qF "${run_url}"; then
        # The run URL rather than the bare id: the digits of a run id turn up
        # in unrelated URLs on a long issue, and the prompt asks for the URL.
        drop_reason="Triage cited issue #${tracking_issue}, but nothing there references ${run_url}."
    fi

    if [ -n "${drop_reason}" ]; then
        echo "merge-ci-triage: ${drop_reason}"
        if [ "${DRY_RUN}" = "true" ]; then
            jq --arg reason "${drop_reason}" '.evidence += [$reason]' \
                "${TRIAGE_JSON}" > "${TRIAGE_JSON}.checked" \
                && mv "${TRIAGE_JSON}.checked" "${TRIAGE_JSON}"
        else
            jq --arg reason "${drop_reason}" \
                '.tracking_issue = null | .tracking_issue_action = "none" |
                 .evidence += [$reason]' \
                "${TRIAGE_JSON}" > "${TRIAGE_JSON}.checked" \
                && mv "${TRIAGE_JSON}.checked" "${TRIAGE_JSON}"
        fi
    fi
fi

exit_status=0
if ! python3 "${TOOLS_DIR}/merge-triage.py" validate "${TRIAGE_JSON}"; then
    # A document that fails our own schema is a bug here, not a triage
    # outcome. It is replaced rather than published, because a consumer
    # reading a malformed document is worse off than one reading an honest
    # "no verdict" -- but the exit status still reports the bug.
    echo "merge-ci-triage: the verdict document does not validate, which is a bug here" >&2
    cp "${TRIAGE_JSON}" "${OUTPUT_DIR}/triage.invalid.json"
    python3 "${TOOLS_DIR}/merge-triage.py" fallback \
        "${ENVELOPE_JSON}" "${TRIAGE_JSON}" \
        'The verdict document did not match the published schema and was discarded.'
    exit_status=1
fi

verdict=$(jq -r '.verdict' "${TRIAGE_JSON}")
recommendation=$(jq -r '.recommendation' "${TRIAGE_JSON}")
echo "merge-ci-triage: verdict ${verdict}, recommendation ${recommendation}"

# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

python3 "${TOOLS_DIR}/merge-triage.py" render \
    "${TRIAGE_JSON}" "${OUTPUT_DIR}/comment.md" || exit 1

# The marker is how a re-run of triage over the same failure recognises its own
# earlier comment. It is invisible in the rendered comment.
marker="<!-- merge-triage run:${RUN_ID} -->"
{ echo "${marker}"; echo; cat "${OUTPUT_DIR}/comment.md"; } > "${OUTPUT_DIR}/comment.body.md"
mv "${OUTPUT_DIR}/comment.body.md" "${OUTPUT_DIR}/comment.md"

# The body is model output. Neutralise the two constructs GitHub acts on
# rather than renders, for the same reason the issue-fix workflow does: the
# prompt forbids both, and a side effect which fires on posting and cannot be
# taken back should not rest on the model having complied. The markup which
# would break the comment's own structure is already gone, stripped by
# merge-triage.py before the document was written.
"${TOOLS_DIR}/neutralise-pr-body.sh" "${OUTPUT_DIR}/comment.md"

if [ "${DRY_RUN}" = "true" ]; then
    echo "merge-ci-triage: dry run, not commenting on the pull request"
    exit "${exit_status}"
fi

if [ -z "${pr_number}" ]; then
    echo "merge-ci-triage: no pull request to comment on"
    exit "${exit_status}"
fi

if gh pr view "${pr_number}" --repo "${REPO}" --json comments \
        --jq '[.comments[].body] | join("\n")' 2>/dev/null \
        | grep -qF "${marker}"; then
    echo "merge-ci-triage: run ${RUN_ID} has already been triaged on #${pr_number}"
    exit "${exit_status}"
fi

gh pr comment "${pr_number}" --repo "${REPO}" --body-file "${OUTPUT_DIR}/comment.md" \
    || echo "merge-ci-triage: could not comment on #${pr_number}" >&2

exit "${exit_status}"
