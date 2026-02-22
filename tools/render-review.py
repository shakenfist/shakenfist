#!/usr/bin/env python3
"""Render structured review JSON to human-readable markdown.

This script takes the JSON output from the automated reviewer and converts
it to a nicely formatted markdown comment suitable for posting on a PR.

Usage:
    render-review.py [--embed-json] <input.json> [output.md]
    render-review.py --validate <input.json>

Options:
    --embed-json    Include the raw JSON in a collapsed <details> section
                    at the end of the markdown. This allows the address-comments
                    automation to extract it from the PR comment.
    --validate      Validate the JSON against the schema without rendering.

If output.md is not specified, writes to stdout.
"""

import json
import sys
from pathlib import Path

# Try to import jsonschema for validation, but don't require it
try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


SCHEMA_PATH = Path(__file__).parent / 'review-schema.json'

SEVERITY_EMOJI = {
    'critical': '\U0001F534',  # Red circle
    'high': '\U0001F7E0',      # Orange circle
    'medium': '\U0001F7E1',    # Yellow circle
    'low': '\U0001F7E2',       # Green circle
}

ACTION_LABEL = {
    'fix': 'FIX',
    'document': 'DOC',
    'consider': 'CONSIDER',
    'none': 'INFO',
}

CATEGORY_EMOJI = {
    'security': '\U0001F512',  # Lock
    'bug': '\U0001F41B',       # Bug
    'performance': '\u26A1',   # Lightning
    'documentation': '\U0001F4DD',  # Memo
    'style': '\U0001F3A8',     # Palette
    'testing': '\U0001F9EA',   # Test tube
    'other': '\U0001F4CB',     # Clipboard
}


def load_schema() -> dict | None:
    """Load the JSON schema for validation."""
    if not SCHEMA_PATH.exists():
        return None
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def validate_review(review_data: dict) -> tuple[bool, str]:
    """Validate review data against the schema.

    Returns (is_valid, error_message).
    """
    if not HAS_JSONSCHEMA:
        # Basic validation without jsonschema
        if 'summary' not in review_data:
            return False, "Missing required field: 'summary'"
        if 'items' not in review_data:
            return False, "Missing required field: 'items'"
        if not isinstance(review_data['items'], list):
            return False, "'items' must be an array"
        for i, item in enumerate(review_data['items']):
            for field in ['id', 'title', 'category', 'action']:
                if field not in item:
                    return False, f"Item {i}: missing required field '{field}'"
            if item['action'] not in ['fix', 'document', 'consider', 'none']:
                return False, f"Item {i}: invalid action '{item['action']}'"
        return True, ''

    schema = load_schema()
    if schema is None:
        return True, ''  # No schema available, skip validation

    try:
        jsonschema.validate(review_data, schema)
        return True, ''
    except jsonschema.ValidationError as e:
        return False, str(e.message)


def render_markdown(review_data: dict, embed_json: bool = False) -> str:
    """Render review data to markdown format.

    Args:
        review_data: The review data dictionary.
        embed_json: If True, append the raw JSON in a collapsed details section
                    for machine parsing by the address-comments automation.
    """
    lines = []

    # Header
    lines.append('## PR Review')
    lines.append('')

    # Summary
    lines.append('### Summary')
    lines.append('')
    lines.append(review_data.get('summary', 'No summary provided.'))
    lines.append('')

    # Separate items by action type
    fix_items = []
    doc_items = []
    consider_items = []
    info_items = []

    for item in review_data.get('items', []):
        action = item.get('action', 'none')
        if action == 'fix':
            fix_items.append(item)
        elif action == 'document':
            doc_items.append(item)
        elif action == 'consider':
            consider_items.append(item)
        else:
            info_items.append(item)

    # Action Items (things that must be done)
    if fix_items or doc_items:
        lines.append('---')
        lines.append('')
        lines.append('### Action Items')
        lines.append('')

        for item in fix_items + doc_items:
            lines.extend(render_item(item))
            lines.append('')

    # Suggestions (optional improvements)
    if consider_items:
        lines.append('---')
        lines.append('')
        lines.append('### Suggestions')
        lines.append('')
        lines.append('*These are optional improvements to consider:*')
        lines.append('')

        for item in consider_items:
            lines.extend(render_item(item))
            lines.append('')

    # Observations (informational only)
    if info_items:
        lines.append('---')
        lines.append('')
        lines.append('### Observations')
        lines.append('')
        lines.append('*These are informational and do not require action:*')
        lines.append('')

        for item in info_items:
            lines.extend(render_item(item))
            lines.append('')

    # Positive feedback
    positive = review_data.get('positive_feedback', [])
    if positive:
        lines.append('---')
        lines.append('')
        lines.append("### What's Good")
        lines.append('')

        for item in positive:
            lines.append(f"\u2705 **{item['title']}**")
            lines.append('')
            lines.append(item.get('description', ''))
            lines.append('')

    # Test coverage
    test_coverage = review_data.get('test_coverage')
    if test_coverage:
        lines.append('---')
        lines.append('')
        lines.append('### Test Coverage')
        lines.append('')

        if test_coverage.get('adequate'):
            lines.append('\u2705 Test coverage appears adequate.')
        else:
            lines.append('\u26A0\uFE0F Test coverage may need improvement.')

        missing = test_coverage.get('missing', [])
        if missing:
            lines.append('')
            lines.append('**Missing test scenarios:**')
            for scenario in missing:
                lines.append(f'- {scenario}')
        lines.append('')

    # Collect issues created for auto-close links
    issue_numbers = []
    for item in review_data.get('items', []):
        if item.get('issue_number'):
            issue_numbers.append(item['issue_number'])

    if issue_numbers:
        lines.append('---')
        lines.append('')
        lines.append('### Related Issues')
        lines.append('')
        lines.append('The following issues were created for this review and '
                     'will be closed when this PR merges:')
        lines.append('')
        for num in issue_numbers:
            lines.append(f'- Closes #{num}')
        lines.append('')

    # Footer
    lines.append('---')
    lines.append('')
    lines.append('*\U0001F916 This review was generated by the automated reviewer. '
                 'Use `@shakenfist-bot please address comments` to have '
                 'Claude Code address the action items.*')

    # Optionally embed the JSON for machine parsing
    if embed_json:
        lines.append('')
        lines.append('<details>')
        lines.append('<summary>Machine-readable review data (for automation)'
                     '</summary>')
        lines.append('')
        lines.append('```json')
        lines.append(json.dumps(review_data, indent=2))
        lines.append('```')
        lines.append('')
        lines.append('</details>')

    return '\n'.join(lines)


def render_item(item: dict) -> list[str]:
    """Render a single review item to markdown lines."""
    lines = []

    # Build title line with emoji and labels
    action = item.get('action', 'none')
    category = item.get('category', 'other')
    severity = item.get('severity')

    action_label = ACTION_LABEL.get(action, 'INFO')
    category_emoji = CATEGORY_EMOJI.get(category, '\U0001F4CB')

    title_parts = [f"**{item['id']}. [{action_label}]**"]

    if severity:
        severity_emoji = SEVERITY_EMOJI.get(severity, '')
        title_parts.append(f'{severity_emoji}')

    title_parts.append(f"{category_emoji} {item['title']}")

    # Add issue link if present
    if item.get('issue_number'):
        title_parts.append(f"(#{item['issue_number']})")

    lines.append(' '.join(title_parts))
    lines.append('')

    # Description
    if item.get('description'):
        lines.append(item['description'])
        lines.append('')

    # Location
    if item.get('location'):
        lines.append(f"\U0001F4CD Location: `{item['location']}`")
        lines.append('')

    # Suggestion
    if item.get('suggestion'):
        lines.append(f"\U0001F4A1 **Suggestion:** {item['suggestion']}")
        lines.append('')

    # Rationale (for none/consider actions)
    if item.get('rationale'):
        lines.append(f"\u2139\uFE0F *{item['rationale']}*")
        lines.append('')

    return lines


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    # Parse arguments
    args = sys.argv[1:]
    embed_json = False

    if '--embed-json' in args:
        embed_json = True
        args.remove('--embed-json')

    # Handle --validate flag
    if args and args[0] == '--validate':
        if len(args) < 2:
            print('Error: --validate requires an input file')
            sys.exit(1)
        input_path = Path(args[1])
        with open(input_path) as f:
            data = json.load(f)
        is_valid, error = validate_review(data)
        if is_valid:
            print('Valid')
            sys.exit(0)
        else:
            print(f'Invalid: {error}')
            sys.exit(1)

    if not args:
        print(__doc__)
        sys.exit(1)

    input_path = Path(args[0])
    output_path = Path(args[1]) if len(args) > 1 else None

    # Load and validate
    with open(input_path) as f:
        data = json.load(f)

    is_valid, error = validate_review(data)
    if not is_valid:
        print(f'Error: Invalid review JSON: {error}', file=sys.stderr)
        sys.exit(1)

    # Render
    markdown = render_markdown(data, embed_json=embed_json)

    # Output
    if output_path:
        with open(output_path, 'w') as f:
            f.write(markdown)
    else:
        print(markdown)


if __name__ == '__main__':
    main()
