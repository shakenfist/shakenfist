#!/bin/bash

# Auto-fix exceptions using Claude Code.
#
# This script analyzes exception JSON files from CI bundles, finds the most
# frequent exception (by traceback), and uses Claude Code to fix it in the
# codebase. If fixes are successful, it commits the changes and pushes to
# the PR branch.
#
# Exception files are named by a hash of the traceback and contain:
#   - traceback: the full Python traceback string
#   - count: number of times this exact traceback occurred
#   - events: list of unix timestamps when it occurred
#
# Usage:
#   tools/fix-exceptions-with-claude.sh [options] [job_names...]
#
# Options:
#   --no-push           Fix and commit but don't push
#   --no-commit         Fix but don't commit or push
#   --max-turns N       Maximum Claude turns (default: 100)
#   --interactive       Run Claude in interactive mode (default: headless)
#   --ci                CI mode: output machine-readable status, no colors
#   --bundles-dir DIR   Directory containing downloaded bundles
#   --help              Show this help message
#
# Arguments:
#   job_names           Space-separated list of failed job names (optional)
#
# Environment Variables:
#   BUNDLES_DIR         Directory containing downloaded bundles (alternative to
#                       --bundles-dir)
#   FAILED_JOBS         Space-separated list of failed job names (alternative
#                       to positional arguments)
#
# Exit codes:
#   0 - No exceptions found or fixes were committed and pushed
#   1 - Exceptions found but could not be fixed
#   2 - Exceptions fixed but push failed
#   3 - No bundles or exception files found
#
# Examples:
#   # Run in CI with bundles already downloaded
#   BUNDLES_DIR=/srv/github/bundles tools/fix-exceptions-with-claude.sh --ci
#
#   # Interactive mode for debugging
#   tools/fix-exceptions-with-claude.sh --interactive --bundles-dir ./bundles
#
#   # Just analyze, don't fix anything
#   tools/fix-exceptions-with-claude.sh --no-commit --bundles-dir ./bundles

set -e

topdir=$(cd "$(dirname "$0")/.." && pwd)
cd "${topdir}"

# Default options
do_push=true
do_commit=true
max_turns=100
interactive=false
ci_mode=false
bundles_dir="${BUNDLES_DIR:-}"
failed_jobs="${FAILED_JOBS:-}"

# Colors for output (disabled in CI mode)
setup_colors() {
    if [ "${ci_mode}" = true ]; then
        RED=''
        GREEN=''
        YELLOW=''
        BLUE=''
        NC=''
    else
        RED='\033[0;31m'
        GREEN='\033[0;32m'
        YELLOW='\033[1;33m'
        BLUE='\033[0;34m'
        NC='\033[0m'
    fi
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-push)
            do_push=false
            shift
            ;;
        --no-commit)
            do_commit=false
            do_push=false
            shift
            ;;
        --max-turns)
            max_turns="$2"
            shift 2
            ;;
        --interactive)
            interactive=true
            shift
            ;;
        --ci)
            ci_mode=true
            shift
            ;;
        --bundles-dir)
            bundles_dir="$2"
            shift 2
            ;;
        --help|-h)
            head -50 "$0" | tail -47
            exit 0
            ;;
        -*)
            echo "Unknown option: $1"
            exit 1
            ;;
        *)
            # Positional arguments are job names
            if [ -z "${failed_jobs}" ]; then
                failed_jobs="$1"
            else
                failed_jobs="${failed_jobs} $1"
            fi
            shift
            ;;
    esac
done

setup_colors

# Validate bundles directory
if [ -z "${bundles_dir}" ]; then
    echo -e "${RED}Error: No bundles directory specified${NC}"
    echo "Use --bundles-dir or set BUNDLES_DIR environment variable"
    exit 3
fi

if [ ! -d "${bundles_dir}" ]; then
    echo -e "${RED}Error: Bundles directory does not exist: ${bundles_dir}${NC}"
    exit 3
fi

# Create working directory
work_dir=$(mktemp -d)
cleanup() {
    rm -rf "${work_dir}"
}
trap cleanup EXIT

# CI mode output helper
ci_output() {
    local key="$1"
    local value="$2"
    if [ "${ci_mode}" = true ]; then
        echo "${key}=${value}"
    fi
}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Shaken Fist Exception Fixer${NC}"
echo -e "${BLUE}========================================${NC}"
echo

# Step 1: Extract bundles and collect exception files
echo -e "${YELLOW}Step 1: Extracting bundles and collecting exception files...${NC}"
echo

exceptions_dir="${work_dir}/exceptions"
mkdir -p "${exceptions_dir}"

for job_dir in "${bundles_dir}"/*/; do
    job_name=$(basename "$job_dir")
    echo "Processing bundle for job: $job_name"

    if [ -f "${job_dir}/bundle.zip" ]; then
        unzip -q "${job_dir}/bundle.zip" -d "${job_dir}"

        # Copy exception files from srv/shakenfist/exceptions if they exist
        # The bundle structure is: bundle/<node_name>/srv/shakenfist/exceptions/
        for node_dir in "${job_dir}"/bundle/*/; do
            exc_dir="${node_dir}srv/shakenfist/exceptions"
            if [ -d "$exc_dir" ]; then
                node_name=$(basename "$node_dir")
                echo "  Found exceptions in node: $node_name"
                # Copy files - same traceback hash from different nodes will
                # have same filename, we'll aggregate counts later
                for f in "$exc_dir"/*.json; do
                    if [ -f "$f" ]; then
                        base=$(basename "$f")
                        # If file already exists, we need to merge counts
                        if [ -f "${exceptions_dir}/${base}" ]; then
                            # Add counts together
                            existing_count=$(jq -r '.count' "${exceptions_dir}/${base}")
                            new_count=$(jq -r '.count' "$f")
                            total_count=$((existing_count + new_count))
                            # Merge events arrays and update count
                            jq -s '.[0] * {count: (.[0].count + .[1].count), events: (.[0].events + .[1].events)}' \
                                "${exceptions_dir}/${base}" "$f" > "${exceptions_dir}/${base}.tmp"
                            mv "${exceptions_dir}/${base}.tmp" "${exceptions_dir}/${base}"
                        else
                            cp "$f" "${exceptions_dir}/${base}"
                        fi
                    fi
                done
            fi
        done
    else
        echo "  Warning: No bundle.zip found in ${job_dir}"
    fi
done

echo
echo "Collected exception files:"
exception_count=$(find "${exceptions_dir}" -name "*.json" 2>/dev/null | wc -l)
echo "  Unique tracebacks: ${exception_count}"

if [ "${exception_count}" -eq 0 ]; then
    echo -e "${GREEN}✓ No exception files found in bundles${NC}"
    ci_output "exceptions_found" "false"
    exit 0
fi

ci_output "exceptions_found" "true"
ci_output "unique_tracebacks" "${exception_count}"

# Step 2: Find the most frequent exception (highest count)
echo
echo -e "${YELLOW}Step 2: Finding the most frequent exception...${NC}"
echo

# List all exceptions with their counts
echo "Exception frequency (by traceback hash):"
for f in "${exceptions_dir}"/*.json; do
    if [ -f "$f" ]; then
        hash=$(basename "$f" .json)
        count=$(jq -r '.count' "$f")
        echo "  ${hash}: ${count} occurrences"
    fi
done | sort -t: -k2 -rn | head -10

echo

# Find the file with the highest count
most_frequent_file=""
highest_count=0

for f in "${exceptions_dir}"/*.json; do
    if [ -f "$f" ]; then
        count=$(jq -r '.count' "$f")
        if [ "${count}" -gt "${highest_count}" ]; then
            highest_count="${count}"
            most_frequent_file="$f"
        fi
    fi
done

if [ -z "${most_frequent_file}" ]; then
    echo -e "${RED}Error: Could not determine most frequent exception${NC}"
    exit 1
fi

traceback_hash=$(basename "${most_frequent_file}" .json)
echo -e "${BLUE}Most frequent exception: ${traceback_hash} (${highest_count} occurrences)${NC}"

ci_output "target_traceback_hash" "${traceback_hash}"
ci_output "target_exception_count" "${highest_count}"

echo
echo "Traceback:"
jq -r '.traceback' "${most_frequent_file}"

# Step 3: Check Claude availability
echo
echo -e "${YELLOW}Step 3: Checking Claude Code availability...${NC}"

claude_bin="${CLAUDE_BIN:-claude}"
if ! command -v "${claude_bin}" &> /dev/null; then
    echo -e "${RED}Error: Claude Code CLI not found (${claude_bin})${NC}"
    echo "Install with: npm install -g @anthropic-ai/claude-code"
    echo "Or set CLAUDE_BIN to the path of an existing install."
    ci_output "claude_available" "false"
    ci_output "fix_succeeded" "false"
    exit 1
fi

ci_output "claude_available" "true"
echo -e "${GREEN}✓ Claude Code is available${NC}"
echo

# Step 4: Build prompt and run Claude
echo -e "${YELLOW}Step 4: Running Claude Code to fix the exception...${NC}"
echo

# Extract the traceback
exc_traceback=$(jq -r '.traceback' "${most_frequent_file}")

# Build the prompt
cat > "${work_dir}/claude-prompt.txt" << PROMPT_EOF
During CI testing, an unhandled exception was detected ${highest_count} times.
This is the most frequent exception found in the test run. Please analyze
and fix the root cause in the codebase.

## Traceback

\`\`\`
${exc_traceback}
\`\`\`

## Your Task

1. Analyze the traceback to understand what caused this exception
2. Find the relevant source file(s) in the codebase
3. Implement a fix that prevents this exception from occurring
4. The fix should handle the error condition gracefully, not just suppress it
5. Consider edge cases and ensure the fix is robust

## Guidelines

- Focus on fixing the ROOT CAUSE, not just adding try/except to hide the error
- If the exception indicates a logic error, fix the logic
- If it's a missing null check, add appropriate validation
- If it's a race condition, add proper synchronization
- Add appropriate logging if the fix involves handling an error condition
- Follow existing code style (single quotes, 80 char lines, etc.)

## After Making Fixes

1. Review your changes to ensure they're correct
2. Only stage your changes with 'git add' - do NOT commit
3. Briefly explain what you fixed and why

## Important Notes

- This exception occurred ${highest_count} times during testing
- Fix only this specific exception, not other issues you might notice
- Be careful not to introduce new bugs while fixing this one
- If you're unsure about the fix, explain your reasoning
PROMPT_EOF

echo "Prompt prepared. Starting Claude Code..."
echo

# Run Claude Code
if [ "${interactive}" = true ]; then
    # Interactive mode
    echo "Prompt file: ${work_dir}/claude-prompt.txt"
    echo
    cat "${work_dir}/claude-prompt.txt"
    echo
    echo "Run 'claude' and paste the prompt above to fix the exception."
    exit 1
else
    # Headless mode - use JSON output to capture turn count and other metadata
    "${claude_bin}" -p "$(cat "${work_dir}/claude-prompt.txt")" \
        --dangerously-skip-permissions \
        --max-turns "${max_turns}" \
        --output-format json > "${work_dir}/claude-output.json" || true

    # Extract and display the result text
    if [ -f "${work_dir}/claude-output.json" ]; then
        jq -r '.result // empty' "${work_dir}/claude-output.json"

        # Extract metadata for CI output
        num_turns=$(jq -r '.num_turns // "unknown"' "${work_dir}/claude-output.json")
        duration_ms=$(jq -r '.duration_ms // "unknown"' "${work_dir}/claude-output.json")
        cost_usd=$(jq -r '.total_cost_usd // "unknown"' "${work_dir}/claude-output.json")

        echo
        echo -e "${BLUE}Claude execution stats:${NC}"
        echo "  Turns: ${num_turns} / ${max_turns}"
        echo "  Duration: ${duration_ms}ms"
        echo "  Cost: \$${cost_usd}"

        ci_output "claude_turns" "${num_turns}"
        ci_output "claude_duration_ms" "${duration_ms}"
        ci_output "claude_cost_usd" "${cost_usd}"
    fi
fi

echo
echo -e "${YELLOW}Step 5: Checking for changes...${NC}"

# Check if Claude made any changes
if git diff --quiet && git diff --staged --quiet; then
    echo -e "${YELLOW}No changes were made by Claude${NC}"
    echo "This could mean:"
    echo "  - Claude couldn't find a fix"
    echo "  - The fix requires more context"
    echo "  - The issue is in external code"
    ci_output "fix_succeeded" "false"
    exit 1
fi

echo -e "${GREEN}✓ Changes detected${NC}"
echo
echo "Changed files:"
git status --short

ci_output "fix_succeeded" "true"
echo

# Step 6: Commit and push if requested
if [ "${do_commit}" = false ]; then
    echo -e "${YELLOW}Skipping commit (--no-commit specified)${NC}"
    echo "Changes made but not committed:"
    git diff
    exit 0
fi

echo -e "${YELLOW}Step 6: Committing fixes...${NC}"

# Stage all Python file changes
git add -A shakenfist/

# Check if there are changes to commit
if git diff --staged --quiet; then
    echo "No changes to commit (fixes may have been staged already)"
else
    # Extract a short description from the traceback (first line of actual error)
    short_desc=$(echo "${exc_traceback}" | grep -E "^[A-Za-z]+Error:|^[A-Za-z]+Exception:" | head -1 | cut -c1-60)
    if [ -z "${short_desc}" ]; then
        short_desc="unhandled exception"
    fi

    # Commit the fixes
    git commit -m "$(cat <<EOF
Fix ${short_desc}.

This exception occurred ${highest_count} times during CI testing.
The fix addresses the root cause identified in the traceback.

Traceback hash: ${traceback_hash}

Assisted-By: Claude Code

Signed-off-by: Claude Code run by Shakenfist Bot <bot@shakenfist.com>
EOF
)"
    echo -e "${GREEN}✓ Changes committed${NC}"
fi

if [ "${do_push}" = false ]; then
    echo -e "${YELLOW}Skipping push (--no-push specified)${NC}"
    exit 0
fi

echo -e "${YELLOW}Step 7: Pushing to remote...${NC}"

# Get current branch - in GitHub Actions PR context, use GITHUB_HEAD_REF
# as the checkout is in detached HEAD state
if [ -n "${GITHUB_HEAD_REF}" ]; then
    current_branch="${GITHUB_HEAD_REF}"
else
    current_branch=$(git rev-parse --abbrev-ref HEAD)
fi

echo "Pushing to branch: ${current_branch}"

# Push to origin
set +e
git push origin "HEAD:${current_branch}"
push_exit_code=$?
set -e

if [ ${push_exit_code} -ne 0 ]; then
    echo -e "${RED}✗ Push failed${NC}"
    ci_output "push_succeeded" "false"
    exit 2
fi

echo -e "${GREEN}✓ Pushed to origin/${current_branch}${NC}"
ci_output "push_succeeded" "true"
echo
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Exception fix committed and pushed!${NC}"
echo -e "${GREEN}========================================${NC}"
