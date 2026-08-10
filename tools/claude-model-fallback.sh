#!/bin/bash

# Run a headless `claude -p` job against a preferred model, falling back to
# other models when the preferred one's subscription credit is exhausted.
#
# Claude Code signals an exhausted per-model allowance as an HTTP 429 whose
# JSON result looks like:
#
#   {"is_error": true, "total_cost_usd": 0, "api_error_status": 429,
#    "result": "You've reached your Fable 5 limit. Run /usage-credits to
#               continue or switch models with /model.", ...}
#
# The built-in --fallback-model flag does NOT cover this case (it only
# handles overloaded or unavailable models), so automated jobs need to
# detect it themselves.
#
# A rejected request is free -- it burns zero tokens and reports
# total_cost_usd of 0 -- so this wrapper attempts the real job rather than
# running a separate pre-flight probe, which would cost real money on every
# run in which the preferred model *is* available.
#
# Usage:
#   tools/claude-model-fallback.sh [options] -- <claude args...>
#   tools/claude-model-fallback.sh --check MODEL
#
# Options:
#   --models LIST  Comma-separated models to try in order
#                  (default: claude-fable-5,claude-opus-5)
#   --quiet        Suppress the "falling back" notice on stderr
#   --check MODEL  Probe MODEL only and exit; 0 = available, 1 = out of
#                  credit. Note that a successful probe costs a small
#                  amount of credit.
#   --help         Show this help message
#
# Do not pass --model yourself; use --models. A caller supplied
# --output-format is honoured but consumed by this wrapper, which always
# asks claude for JSON internally (that is what carries the 429 signal) and
# reshapes the output afterwards. Formats "text" (the default) and "json"
# are supported, "stream-json" is not, because a fallback decision cannot be
# unwound once streamed output has been emitted.
#
# Exit codes:
#   0    a model succeeded
#   1    every model in the list was out of credit
#   2    usage error
#   *    the underlying claude exit code for any non-credit failure

set -uo pipefail

models='claude-fable-5,claude-opus-5'
quiet=0
check_model=''
output_format='text'
claude_args=()

usage() {
    cat << 'USAGE_EOF'
Usage:
  tools/claude-model-fallback.sh [options] -- <claude args...>
  tools/claude-model-fallback.sh --check MODEL

Options:
  --models LIST  Comma-separated models to try in order
                 (default: claude-fable-5,claude-opus-5)
  --quiet        Suppress the "falling back" notice on stderr
  --check MODEL  Probe MODEL only and exit; 0 = available, 1 = out of credit
  --help         Show this help message

Do not pass --model yourself; use --models. Output formats "text" (the
default) and "json" are supported, "stream-json" is not.

Exit codes:
  0    a model succeeded
  1    every model in the list was out of credit
  2    usage error
  *    the underlying claude exit code for any non-credit failure
USAGE_EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --models)
            [ $# -ge 2 ] || { echo 'claude-model-fallback: --models needs a value' >&2; exit 2; }
            models="$2"
            shift 2
            ;;
        --models=*)
            models="${1#*=}"
            shift
            ;;
        --quiet)
            quiet=1
            shift
            ;;
        --check)
            [ $# -ge 2 ] || { echo 'claude-model-fallback: --check needs a model' >&2; exit 2; }
            check_model="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        *)
            break
            ;;
    esac
done

command -v jq > /dev/null || { echo 'claude-model-fallback: jq is required' >&2; exit 2; }

# --check: probe a single model with a throwaway prompt.
if [ -n "${check_model}" ]; then
    out=$(claude -p --model "${check_model}" --output-format json --tools '' \
        --disable-slash-commands --no-session-persistence 'Reply with: ok' 2>/dev/null)
    status=$(jq -r '.api_error_status // empty' <<< "${out}" 2>/dev/null)
    if [ "${status}" = '429' ]; then
        [ "${quiet}" -eq 1 ] || jq -r '.result' <<< "${out}" >&2
        exit 1
    fi
    exit 0
fi

# Split the caller's claude arguments from the output format, which this
# wrapper owns: claude is always asked for JSON (that is what carries the
# 429 signal) and the output is reshaped afterwards. Leaving a caller's
# --output-format in place would override the internal request and break
# credit detection.
while [ $# -gt 0 ]; do
    case "$1" in
        --output-format)
            [ $# -ge 2 ] || { echo 'claude-model-fallback: --output-format needs a value' >&2; exit 2; }
            output_format="$2"
            shift 2
            ;;
        --output-format=*)
            output_format="${1#*=}"
            shift
            ;;
        --model|--model=*)
            echo 'claude-model-fallback: do not pass --model, use --models' >&2
            exit 2
            ;;
        *)
            claude_args+=("$1")
            shift
            ;;
    esac
done

case "${output_format}" in
    text|json)
        ;;
    *)
        echo "claude-model-fallback: unsupported --output-format '${output_format}'" >&2
        exit 2
        ;;
esac

[ "${#claude_args[@]}" -gt 0 ] || {
    echo 'claude-model-fallback: no claude arguments given' >&2
    exit 2
}

rc=0
IFS=',' read -ra model_list <<< "${models}"
for model in "${model_list[@]}"; do
    out=$(claude -p --output-format json --model "${model}" "${claude_args[@]}")
    rc=$?

    status=$(jq -r '.api_error_status // empty' <<< "${out}" 2>/dev/null)
    if [ "${status}" = '429' ]; then
        if [ "${quiet}" -eq 0 ]; then
            printf 'claude-model-fallback: %s out of credit: %s\n' \
                "${model}" "$(jq -r '.result' <<< "${out}")" >&2
        fi
        continue
    fi

    # Anything else -- success, or a failure unrelated to credit -- is the
    # caller's answer. Falling back would not help and would cost money.
    if [ "${output_format}" = 'json' ]; then
        printf '%s\n' "${out}"
    else
        jq -r '.result // empty' <<< "${out}" 2>/dev/null || printf '%s\n' "${out}"
    fi
    exit ${rc}
done

echo "claude-model-fallback: every model in '${models}' is out of credit" >&2
exit 1
