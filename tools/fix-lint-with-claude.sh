#!/bin/bash

# Auto-fix lint errors using Claude Code.
#
# This script runs flake8, captures errors, and uses Claude Code to fix them.
# If fixes are successful, it commits the changes and pushes to the PR branch.
#
# Usage:
#   tools/fix-lint-with-claude.sh [options]
#
# Options:
#   --no-push           Fix and commit but don't push
#   --no-commit         Fix but don't commit or push
#   --max-turns N       Maximum Claude turns (default: 100)
#   --interactive       Run Claude in interactive mode (default: headless)
#   --ci                CI mode: output machine-readable status, no colors
#   --output-dir DIR    Directory for output files (default: temp dir)
#   --help              Show this help message
#
# Exit codes:
#   0 - Lint passed (no errors) or fixes were committed and pushed
#   1 - Lint errors found and could not be fixed
#   2 - Lint errors fixed but push failed
#
# Examples:
#   # Run in CI (headless, commit and push fixes)
#   tools/fix-lint-with-claude.sh --ci
#
#   # Interactive mode for debugging
#   tools/fix-lint-with-claude.sh --interactive --no-push
#
#   # Just fix, don't commit anything
#   tools/fix-lint-with-claude.sh --no-commit

set -e

topdir=$(cd "$(dirname "$0")/.." && pwd)
cd "${topdir}"

# Default options
do_push=true
do_commit=true
max_turns=100
interactive=false
ci_mode=false
output_dir=""

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
        --output-dir)
            output_dir="$2"
            shift 2
            ;;
        --help|-h)
            head -35 "$0" | tail -32
            exit 0
            ;;
        -*)
            echo "Unknown option: $1"
            exit 1
            ;;
        *)
            shift
            ;;
    esac
done

setup_colors

# Create output directory
if [ -z "${output_dir}" ]; then
    output_dir=$(mktemp -d)
    cleanup_output=true
else
    mkdir -p "${output_dir}"
    cleanup_output=false
fi

cleanup() {
    if [ "${cleanup_output}" = true ]; then
        rm -rf "${output_dir}"
    fi
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
echo -e "${BLUE}Shaken Fist Auto-Delinter${NC}"
echo -e "${BLUE}========================================${NC}"
echo

# Step 1: Create venv and run flake8 via tox
echo -e "${YELLOW}Step 1: Running flake8 via tox to capture lint errors...${NC}"
echo

# Create venv if needed
if [ ! -d "/tmp/venv-lint" ]; then
    python3 -m venv /tmp/venv-lint
    . /tmp/venv-lint/bin/activate
    pip install -q uv
    uv pip install -q tox tox-uv
else
    . /tmp/venv-lint/bin/activate
fi

# Run flake8 via tox (which uses flake8wrap.sh -HEAD to check only changes
# since HEAD~1 and excludes generated protobuf code)
# Use -q to reduce tox verbosity and only show flake8 output
set +e
tox -q -eflake8 > "${output_dir}/flake8-errors.txt" 2>&1
flake8_exit_code=$?
set -e

if [ ${flake8_exit_code} -eq 0 ]; then
    echo -e "${GREEN}✓ No lint errors found!${NC}"
    ci_output "lint_passed" "true"
    exit 0
fi

echo -e "${RED}✗ Lint errors found${NC}"
ci_output "lint_passed" "false"
echo

error_count=$(wc -l < "${output_dir}/flake8-errors.txt")
echo "Found ${error_count} lint errors:"
echo
cat "${output_dir}/flake8-errors.txt"
echo

# Step 2: Check Claude availability
echo -e "${YELLOW}Step 2: Checking Claude Code availability...${NC}"

if ! command -v claude &> /dev/null; then
    echo -e "${RED}Error: Claude Code CLI not found${NC}"
    echo "Install with: npm install -g @anthropic-ai/claude-code"
    ci_output "claude_available" "false"
    ci_output "fix_succeeded" "false"
    exit 1
fi

ci_output "claude_available" "true"
echo -e "${GREEN}✓ Claude Code is available${NC}"
echo

# Step 3: Build prompt and run Claude
echo -e "${YELLOW}Step 3: Running Claude Code to fix lint errors...${NC}"
echo

# Build the prompt
cat > "${output_dir}/claude-prompt.txt" << 'PROMPT_EOF'
The flake8 linter has found errors in files changed by this PR. Please fix
all of the lint errors listed below. These errors are only from files
modified in the current commit (not pre-existing errors).

## Lint Errors

PROMPT_EOF

# Append flake8 errors
cat "${output_dir}/flake8-errors.txt" >> "${output_dir}/claude-prompt.txt"

cat >> "${output_dir}/claude-prompt.txt" << 'PROMPT_EOF'

## Your Task

1. Read each error and fix it in the source file
2. Common fixes:
   - E501: Line too long - break line appropriately
   - E302/E303: Wrong number of blank lines - add or remove blank lines
   - E401: Multiple imports on one line - split onto separate lines
   - E711/E712: Comparison to None/True/False - use 'is' or 'is not'
   - W291/W293: Trailing whitespace - remove it
   - F401: Module imported but unused - remove the import
   - F841: Local variable assigned but never used - remove or use it

3. Follow these code style rules:
   - Use single quotes for strings except docstrings (use double quotes)
   - Wrap lines at 80 characters where possible (120 max)
   - Trim trailing whitespace

4. After making fixes, verify by running:
   tox -eflake8

5. Only stage your changes with 'git add' - do NOT commit

## Important Notes

- Fix ALL errors listed above, not just some of them
- Be careful not to change code logic, only style issues
- If an import is "unused" but needed for side effects, add a noqa comment
- Test that your changes don't break the code
PROMPT_EOF

echo "Prompt prepared. Starting Claude Code..."
echo

# Run Claude Code
if [ "${interactive}" = true ]; then
    # Interactive mode
    echo "Prompt file: ${output_dir}/claude-prompt.txt"
    echo
    cat "${output_dir}/claude-prompt.txt"
    echo
    echo "Run 'claude' and paste the prompt above to fix lint errors interactively."
    exit 1
else
    # Headless mode - use JSON output to capture turn count and other metadata
    ~/local/.bin/claude -p "$(cat "${output_dir}/claude-prompt.txt")" \
        --dangerously-skip-permissions \
        --max-turns "${max_turns}" \
        --output-format json > "${output_dir}/claude-output.json" || true

    # Extract and display the result text
    if [ -f "${output_dir}/claude-output.json" ]; then
        jq -r '.result // empty' "${output_dir}/claude-output.json"

        # Extract metadata for CI output
        num_turns=$(jq -r '.num_turns // "unknown"' "${output_dir}/claude-output.json")
        duration_ms=$(jq -r '.duration_ms // "unknown"' "${output_dir}/claude-output.json")
        cost_usd=$(jq -r '.total_cost_usd // "unknown"' "${output_dir}/claude-output.json")

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
echo -e "${YELLOW}Step 4: Verifying fix...${NC}"

# Re-run flake8 via tox (quiet mode)
set +e
tox -q -eflake8 > "${output_dir}/flake8-verify.txt" 2>&1
verify_exit_code=$?
set -e

if [ ${verify_exit_code} -ne 0 ]; then
    remaining_errors=$(wc -l < "${output_dir}/flake8-verify.txt")
    echo -e "${RED}✗ ${remaining_errors} lint errors remain:${NC}"
    cat "${output_dir}/flake8-verify.txt"
    ci_output "fix_succeeded" "false"
    exit 1
fi

echo -e "${GREEN}✓ All lint errors fixed!${NC}"
ci_output "fix_succeeded" "true"
echo

# Step 5: Commit and push if requested
if [ "${do_commit}" = false ]; then
    echo -e "${YELLOW}Skipping commit (--no-commit specified)${NC}"
    echo "Changes made but not committed:"
    git status --short
    exit 0
fi

echo -e "${YELLOW}Step 5: Committing fixes...${NC}"

# Stage all Python file changes
git add -A shakenfist/

# Check if there are changes to commit
if git diff --staged --quiet; then
    echo "No changes to commit (fixes may have been staged already)"
else
    # Commit the fixes
    git commit -m "$(cat <<'EOF'
Fix lint errors detected by flake8.

Automated fix of flake8 lint errors including line length, whitespace,
import ordering, and other style issues.

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

echo -e "${YELLOW}Step 6: Pushing to remote...${NC}"

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
echo -e "${GREEN}Lint fixes committed and pushed!${NC}"
echo -e "${GREEN}========================================${NC}"
