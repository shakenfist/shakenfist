#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# Triage a failed merge queue CI run with Claude Code.
#
#   merge-ci-triage.sh <run id>
#
# Reproduces, unattended, what a maintainer does by hand with the
# merge-ci-triage skill: work out whether a merge_group failure was caused by
# the pull request in the merge group or by something systemic, record a
# systemic failure against its tracking issue, and say whether the pull
# request should be re-queued as-is or fixed first.
#
# Environment:
#   REPO            owner/repo to triage in (default: GITHUB_REPOSITORY)
#   OUTPUT_DIR      where evidence, the prompt and triage.json are written
#                   (default: a mktemp directory, printed on exit)
#   MODELS          comma separated models to try, in preference order
#   MAX_TURNS       turn cap for the triage run
#   DRY_RUN         "true" to classify without writing anything to GitHub
#   TRIAGE_RUN_URL  URL of the run doing the triage, recorded in the verdict
#   GH_TOKEN        token for gh, as usual
#
# The deliverable is $OUTPUT_DIR/triage.json, a document matching
# tools/merge-triage-schema.json. It is written whatever happens -- a model
# which produces nothing usable yields a verdict of "unknown" rather than no
# file -- because the private-ci conductor consumes these to track which merge
# failures have been triaged and which of them blamed the pull request. A
# missing document is indistinguishable from a triage that never ran; an
# "unknown" one is not.
#
# Two things the model reports are checked rather than believed, on the same
# argument the issue-fix workflow makes when it re-runs the test suite the
# model claims to have run:
#
# - The envelope (repository, run id, pull request number) is written here from
#   what GitHub said, and overwrites whatever the model put in those fields.
# - A tracking issue the model says it commented on has to exist, and has to
#   carry a reference to this run. A verdict which cites an issue that does not
#   mention the failure is downgraded to "no issue", because the conductor
#   treats a cited issue as evidence the occurrence was recorded.
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

# ---------------------------------------------------------------------------
# What are we looking at?
# ---------------------------------------------------------------------------

if ! gh run view "${RUN_ID}" --repo "${REPO}" \
        --json event,conclusion,headBranch,headSha,url,attempt,workflowName,createdAt \
        > "${OUTPUT_DIR}/run.json"; then
    echo "merge-ci-triage: could not read run ${RUN_ID} in ${REPO}" >&2
    exit 1
fi

run_event=$(jq -r '.event // ""' "${OUTPUT_DIR}/run.json")
run_conclusion=$(jq -r '.conclusion // ""' "${OUTPUT_DIR}/run.json")
head_branch=$(jq -r '.headBranch // ""' "${OUTPUT_DIR}/run.json")
head_sha=$(jq -r '.headSha // ""' "${OUTPUT_DIR}/run.json")
run_url=$(jq -r '.url // ""' "${OUTPUT_DIR}/run.json")
run_attempt=$(jq -r '.attempt // 1' "${OUTPUT_DIR}/run.json")

echo "merge-ci-triage: ${REPO} run ${RUN_ID} (${run_event}, ${run_conclusion}) on ${head_branch}"

# The workflow filters on these too, but this script is also run by hand
# against a run id, and triaging a green run or a pull request run would
# produce a confident verdict about nothing.
if [ "${run_event}" != "merge_group" ] || [ "${run_conclusion}" != "failure" ]; then
    echo "merge-ci-triage: not a failed merge_group run, nothing to triage" >&2
    exit 0
fi

# gh-readonly-queue/<base>/pr-<number>-<sha>. The queue on the branches this
# runs against is serial, so a merge group holds exactly one pull request and
# the number in the ref is it. A parse failure is survivable: triage still runs
# and the verdict simply has no pull request to attach itself to.
base_branch=$(echo "${head_branch}" | sed -n 's|^gh-readonly-queue/\(.*\)/pr-[0-9][0-9]*-[0-9a-f]*$|\1|p')
pr_number=$(echo "${head_branch}" | sed -n 's|^gh-readonly-queue/.*/pr-\([0-9][0-9]*\)-[0-9a-f]*$|\1|p')
if [ -z "${pr_number}" ]; then
    echo "merge-ci-triage: could not parse a pull request number out of '${head_branch}'" >&2
fi

# ---------------------------------------------------------------------------
# Gather the evidence the skill's step 1 asks for
# ---------------------------------------------------------------------------

gh run view "${RUN_ID}" --repo "${REPO}" --json jobs \
    --jq '[.jobs[] | select(.conclusion == "failure") |
           {job: .name, url: .url,
            failed_steps: [.steps[] | select(.conclusion == "failure") | .name]}]' \
    > "${OUTPUT_DIR}/failed-jobs.json" 2>/dev/null || echo '[]' > "${OUTPUT_DIR}/failed-jobs.json"

# --log-failed is only the failed steps, which is the part worth reading, but
# a cluster build can still emit megabytes of it. The cap is on bytes rather
# than lines because one wrapped ansible line can be thousands of characters.
gh run view "${RUN_ID}" --repo "${REPO}" --log-failed 2>/dev/null \
    | head -c 120000 > "${OUTPUT_DIR}/failed-logs.txt" || true

# Sibling merge groups, for the "is this happening to everybody?" question
# which is what separates systemic from PR-caused more reliably than the log
# does.
gh run list --repo "${REPO}" --event merge_group --limit 20 \
    --json databaseId,conclusion,headBranch,createdAt,url \
    > "${OUTPUT_DIR}/sibling-runs.json" 2>/dev/null || echo '[]' > "${OUTPUT_DIR}/sibling-runs.json"

if [ -n "${pr_number}" ]; then
    gh pr view "${pr_number}" --repo "${REPO}" --json number,title,author,files,body \
        > "${OUTPUT_DIR}/pr.json" 2>/dev/null || echo '{}' > "${OUTPUT_DIR}/pr.json"
else
    echo '{}' > "${OUTPUT_DIR}/pr.json"
fi

# ---------------------------------------------------------------------------
# Build the prompt
# ---------------------------------------------------------------------------

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
- Merge commit: ${head_sha}
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

   Your comment or issue body MUST contain the run URL. It is what a later
   triage matches on to avoid recording the same occurrence twice.

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

  - tracking_issue is the issue number you commented on or created, or null.
    It is checked: an issue which does not exist, or which carries no
    reference to this run, is dropped from the verdict.
  - recommendation is requeue for a systemic failure the pull request cannot
    fix, fix_first when the pull request needs a change, investigate when a
    human has to decide.
  - Do not put an @ in front of any username anywhere in your output, and do
    not write "fixes", "closes" or "resolves" before an issue number. Both do
    something irreversible when this is posted.

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

# ---------------------------------------------------------------------------
# Run the triage
# ---------------------------------------------------------------------------

echo "merge-ci-triage: running triage with models ${MODELS}"
"${TOOLS_DIR}/claude-model-fallback.sh" \
    --models "${MODELS}" \
    -- "$(cat "${OUTPUT_DIR}/prompt.txt")" \
    --dangerously-skip-permissions \
    --max-turns "${MAX_TURNS}" \
    --output-format text \
    2>&1 | tee "${OUTPUT_DIR}/response.txt" \
    || true

# ---------------------------------------------------------------------------
# Turn the response into a verdict document
# ---------------------------------------------------------------------------

jq -n \
    --arg repository "${REPO}" \
    --arg run_url "${run_url}" \
    --arg head_branch "${head_branch}" \
    --arg head_sha "${head_sha}" \
    --arg base_branch "${base_branch}" \
    --arg triage_run_url "${TRIAGE_RUN_URL}" \
    --argjson run_id "${RUN_ID}" \
    --argjson run_attempt "${run_attempt}" \
    --argjson pull_request "${pr_number:-null}" \
    '{repository: $repository, run_id: $run_id, run_url: $run_url,
      run_attempt: $run_attempt, head_branch: $head_branch, head_sha: $head_sha,
      base_branch: (if $base_branch == "" then null else $base_branch end),
      pull_request: $pull_request,
      triage_run_url: (if $triage_run_url == "" then null else $triage_run_url end)}' \
    > "${OUTPUT_DIR}/envelope.json"

python3 "${TOOLS_DIR}/merge-triage.py" extract \
    "${OUTPUT_DIR}/response.txt" "${OUTPUT_DIR}/envelope.json" "${OUTPUT_DIR}/triage.json"
extract_status=$?
if [ "${extract_status}" -ne 0 ]; then
    echo "merge-ci-triage: triage produced no verdict, publishing the fallback document"
fi

# Check the tracking issue rather than believing it. A cited issue is how the
# conductor knows an occurrence was recorded, so a citation which is not backed
# by a comment naming this run is worse than no citation at all.
tracking_issue=$(jq -r '.tracking_issue // empty' "${OUTPUT_DIR}/triage.json")
if [ -n "${tracking_issue}" ]; then
    issue_text=""
    if gh issue view "${tracking_issue}" --repo "${REPO}" \
            --json body,comments > "${OUTPUT_DIR}/tracking-issue.json" 2>/dev/null; then
        issue_text=$(jq -r '.body + "\n" + ([.comments[]?.body] | join("\n"))' \
            "${OUTPUT_DIR}/tracking-issue.json")
    fi

    drop_reason=""
    if [ -z "${issue_text}" ]; then
        drop_reason="Triage cited issue #${tracking_issue}, which could not be read in ${REPO}."
    elif ! echo "${issue_text}" | grep -qF "${RUN_ID}"; then
        drop_reason="Triage cited issue #${tracking_issue}, but nothing on that issue references run ${RUN_ID}."
    fi

    if [ -n "${drop_reason}" ] && [ "${DRY_RUN}" != "true" ]; then
        echo "merge-ci-triage: ${drop_reason}"
        jq --arg reason "${drop_reason}" \
            '.tracking_issue = null | .tracking_issue_action = "none" |
             .evidence += [$reason]' \
            "${OUTPUT_DIR}/triage.json" > "${OUTPUT_DIR}/triage.checked.json" \
            && mv "${OUTPUT_DIR}/triage.checked.json" "${OUTPUT_DIR}/triage.json"
    fi
fi

if ! python3 "${TOOLS_DIR}/merge-triage.py" validate "${OUTPUT_DIR}/triage.json"; then
    echo "merge-ci-triage: the verdict document does not validate, which is a bug here" >&2
    exit 1
fi

verdict=$(jq -r '.verdict' "${OUTPUT_DIR}/triage.json")
recommendation=$(jq -r '.recommendation' "${OUTPUT_DIR}/triage.json")
echo "merge-ci-triage: verdict ${verdict}, recommendation ${recommendation}"

# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

python3 "${TOOLS_DIR}/merge-triage.py" render \
    "${OUTPUT_DIR}/triage.json" "${OUTPUT_DIR}/comment.md" || exit 1

# The marker is how a re-run of triage over the same failure recognises its own
# earlier comment. It is invisible in the rendered comment.
marker="<!-- merge-triage run:${RUN_ID} -->"
{ echo "${marker}"; echo; cat "${OUTPUT_DIR}/comment.md"; } > "${OUTPUT_DIR}/comment.body.md"
mv "${OUTPUT_DIR}/comment.body.md" "${OUTPUT_DIR}/comment.md"

# The body is model output. Neutralise the two constructs GitHub acts on
# rather than renders, for the same reason the issue-fix workflow does: the
# prompt forbids both, and a side effect which fires on posting and cannot be
# taken back should not rest on the model having complied.
"${TOOLS_DIR}/neutralise-pr-body.sh" "${OUTPUT_DIR}/comment.md"

if [ "${DRY_RUN}" = "true" ]; then
    echo "merge-ci-triage: dry run, not commenting on the pull request"
    exit 0
fi

if [ -z "${pr_number}" ]; then
    echo "merge-ci-triage: no pull request to comment on"
    exit 0
fi

already_posted=false
if gh pr view "${pr_number}" --repo "${REPO}" --json comments \
        --jq '[.comments[].body] | join("\n")' 2>/dev/null \
        | grep -qF "${marker}"; then
    already_posted=true
fi

if [ "${already_posted}" = "true" ]; then
    echo "merge-ci-triage: run ${RUN_ID} has already been triaged on #${pr_number}"
    exit 0
fi

gh pr comment "${pr_number}" --repo "${REPO}" --body-file "${OUTPUT_DIR}/comment.md" \
    || echo "merge-ci-triage: could not comment on #${pr_number}" >&2

exit 0
