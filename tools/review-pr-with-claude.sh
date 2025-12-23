#!/bin/bash

# Review a PR using Claude Code.
#
# This script fetches the PR diff and uses Claude Code to provide a code review.
# The review is posted as a PR comment using the GitHub CLI.
#
# Usage:
#   tools/review-pr-with-claude.sh [options]
#
# Options:
#   --pr NUMBER         PR number to review (required in CI, auto-detected locally)
#   --max-turns N       Maximum Claude turns (default: 20)
#   --interactive       Run Claude in interactive mode (default: headless)
#   --ci                CI mode: output machine-readable status, no colors
#   --dry-run           Don't post the review, just print it
#   --output-dir DIR    Directory for output files (default: temp dir)
#   --help              Show this help message
#
# Environment:
#   GITHUB_TOKEN        Required for posting reviews
#   GITHUB_REPOSITORY   Repository in owner/repo format (set by GitHub Actions)
#
# Exit codes:
#   0 - Review posted successfully
#   1 - Error occurred
#
# Examples:
#   # Review PR #123
#   tools/review-pr-with-claude.sh --pr 123
#
#   # CI mode (PR number from environment)
#   tools/review-pr-with-claude.sh --ci
#
#   # Dry run to see what would be posted
#   tools/review-pr-with-claude.sh --pr 123 --dry-run

set -e

topdir=$(cd "$(dirname "$0")/.." && pwd)
cd "${topdir}"

# Default options
pr_number=""
max_turns=20
interactive=false
ci_mode=false
dry_run=false
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
        --pr)
            pr_number="$2"
            shift 2
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
        --dry-run)
            dry_run=true
            shift
            ;;
        --output-dir)
            output_dir="$2"
            shift 2
            ;;
        --help|-h)
            head -38 "$0" | tail -35
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
echo -e "${BLUE}Shaken Fist PR Reviewer${NC}"
echo -e "${BLUE}========================================${NC}"
echo

# Step 1: Validate environment
echo -e "${YELLOW}Step 1: Validating environment...${NC}"

if ! command -v gh &> /dev/null; then
    echo -e "${RED}Error: GitHub CLI (gh) not found${NC}"
    exit 1
fi

if ! command -v claude &> /dev/null; then
    echo -e "${RED}Error: Claude Code CLI not found${NC}"
    exit 1
fi

# Get PR number if not provided
if [ -z "${pr_number}" ]; then
    # Try to get from GitHub Actions event
    if [ -n "${GITHUB_EVENT_PATH}" ] && [ -f "${GITHUB_EVENT_PATH}" ]; then
        pr_number=$(jq -r '.pull_request.number // .number // empty' "${GITHUB_EVENT_PATH}" 2>/dev/null || true)
    fi

    # Try to get from current branch
    if [ -z "${pr_number}" ]; then
        pr_number=$(gh pr view --json number -q '.number' 2>/dev/null || true)
    fi

    if [ -z "${pr_number}" ]; then
        echo -e "${RED}Error: Could not determine PR number${NC}"
        echo "Use --pr NUMBER to specify explicitly"
        exit 1
    fi
fi

echo -e "${GREEN}✓ Reviewing PR #${pr_number}${NC}"
echo

# Step 2: Fetch PR information
echo -e "${YELLOW}Step 2: Fetching PR information...${NC}"

# Get PR details
gh pr view "${pr_number}" --json title,body,author,baseRefName,headRefName \
    > "${output_dir}/pr-info.json"

pr_title=$(jq -r '.title' "${output_dir}/pr-info.json")
pr_author=$(jq -r '.author.login' "${output_dir}/pr-info.json")
base_branch=$(jq -r '.baseRefName' "${output_dir}/pr-info.json")
head_branch=$(jq -r '.headRefName' "${output_dir}/pr-info.json")

echo "Title: ${pr_title}"
echo "Author: ${pr_author}"
echo "Branch: ${head_branch} -> ${base_branch}"
echo

# Get the diff
echo -e "${YELLOW}Step 3: Fetching PR diff...${NC}"
gh pr diff "${pr_number}" > "${output_dir}/pr-diff.txt"

diff_lines=$(wc -l < "${output_dir}/pr-diff.txt")
echo "Diff size: ${diff_lines} lines"
echo

# Check if diff is too large
if [ "${diff_lines}" -gt 5000 ]; then
    echo -e "${YELLOW}Warning: Large diff (${diff_lines} lines), review may be limited${NC}"
fi

# Step 4: Check for existing bot reviews
echo -e "${YELLOW}Step 4: Checking for existing reviews...${NC}"

existing_review=$(gh pr view "${pr_number}" --json reviews \
    --jq '.reviews[] | select(.author.login == "github-actions[bot]" or .author.login == "shakenfist-bot") | .id' \
    2>/dev/null | head -1 || true)

if [ -n "${existing_review}" ]; then
    echo -e "${YELLOW}Note: Bot has already reviewed this PR${NC}"
    echo "Proceeding with new review anyway..."
fi
echo

# Step 5: Run Claude Code for review
echo -e "${YELLOW}Step 5: Running Claude Code for review...${NC}"
echo

# Build the prompt
cat > "${output_dir}/claude-prompt.txt" << PROMPT_EOF
You are reviewing Pull Request #${pr_number} for the Shaken Fist project.

## PR Information

- **Title**: ${pr_title}
- **Author**: ${pr_author}
- **Branch**: ${head_branch} -> ${base_branch}

## Your Task

1. Read the PR diff below carefully
2. Analyze the changes for:
   - Code quality and readability
   - Potential bugs or logic errors
   - Security concerns (SQL injection, command injection, etc.)
   - Performance implications
   - Test coverage (are new features tested?)
   - Documentation (are changes documented?)
   - Style consistency with the codebase

3. Write a constructive review that:
   - Starts with a brief summary of what the PR does
   - Lists specific concerns with file:line references where applicable
   - Suggests improvements where relevant
   - Acknowledges good practices you observe
   - Is professional and helpful in tone

4. Post your review using this exact command:
   gh pr review ${pr_number} --comment --body "<your review here>"

   IMPORTANT: The review body must be properly escaped for the shell.
   Use a heredoc if the review contains quotes or special characters:

   gh pr review ${pr_number} --comment --body "\$(cat <<'REVIEW_EOF'
   Your review content here...
   REVIEW_EOF
   )"

## Code Style Notes for Shaken Fist

- Python code uses single quotes for strings, double quotes for docstrings
- Line length limit is 80 chars (120 max)
- Type hints are encouraged but not required everywhere

## The PR Diff

PROMPT_EOF

# Append the diff
cat "${output_dir}/pr-diff.txt" >> "${output_dir}/claude-prompt.txt"

if [ "${interactive}" = true ]; then
    echo "Prompt file: ${output_dir}/claude-prompt.txt"
    echo
    echo "Run 'claude' and paste the prompt to review the PR interactively."
    exit 0
fi

if [ "${dry_run}" = true ]; then
    echo "Dry run mode - would send this prompt to Claude:"
    echo "---"
    head -50 "${output_dir}/claude-prompt.txt"
    echo "..."
    echo "---"
    echo
    echo "Then Claude would post a review to PR #${pr_number}"
    exit 0
fi

# Run Claude Code
claude -p "$(cat "${output_dir}/claude-prompt.txt")" \
    --dangerously-skip-permissions \
    --max-turns "${max_turns}" \
    --output-format text || true

echo
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}PR review complete!${NC}"
echo -e "${GREEN}========================================${NC}"
ci_output "review_posted" "true"
