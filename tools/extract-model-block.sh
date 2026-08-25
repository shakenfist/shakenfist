#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# Extract a marker delimited block from captured model output.
#
#   extract-model-block.sh <BLOCK_NAME> <input file> <output file>
#
# Reads <input file> looking for the first complete
# <BLOCK_NAME>_START ... <BLOCK_NAME>_END pair and writes what lies
# between them to <output file>. Exits 0 if a non-empty block was
# written and 1 otherwise; the output file is always created, so a
# caller uploading it as a build artifact does not have to special
# case the failure.
#
# The rules below each exist because the naive version of this got one
# of them wrong:
#
# - The block is the text between the LAST start marker and the FIRST
#   end marker which follows it, buffered and emitted only once that
#   end marker is seen. Every awkward shape then fails safe rather than
#   into the published output: a second complete block is ignored, a
#   repeated start marker restarts rather than embedding a marker line
#   in the prose, an end marker before any start is not a close, and a
#   start which is never closed yields nothing rather than the
#   remainder of the transcript -- which, now that there are two
#   blocks, would be the other one. A sed address range does none of
#   this: it re-matches, so repeated markers concatenate and the
#   markers themselves survive into the result.
# - A marker matches only a line whose entire content is the marker
#   token, ignoring surrounding whitespace. Prose which mentions a
#   marker mid-sentence is therefore not a terminator; this file is
#   itself a plausible subject for an automated fix whose description
#   would name these tokens.
# - A code fence is stripped only when it wraps the whole block. The
#   prompt illustrates the blocks inside fences while telling the model
#   not to use them, so a model which copies the illustration would
#   otherwise have its entire pull request body rendered as one
#   preformatted lump. Stripping fences globally is not an option: a
#   description may legitimately contain fenced code.

set -e

if [ $# -ne 3 ]; then
    echo "usage: $(basename "$0") <BLOCK_NAME> <input file> <output file>" >&2
    exit 2
fi

block="$1"
input="$2"
output="$3"

: > "${output}"

if [ ! -f "${input}" ]; then
    echo "extract-model-block: no such input file: ${input}" >&2
    exit 1
fi

# awk's exit in a main rule falls through to END, which is where the
# buffer is emitted and the "no closing marker" status is set.
if ! awk -v start="${block}_START" -v end="${block}_END" '
    function trim(s) {
        sub(/^[[:space:]]+/, "", s)
        sub(/[[:space:]]+$/, "", s)
        return s
    }
    {
        line = trim($0)
        if (line == start) { inblock = 1; body = ""; next }
        if (inblock && line == end) { closed = 1; exit }
        if (inblock) { body = body $0 "\n" }
    }
    END {
        if (!closed) { exit 1 }
        printf "%s", body
    }
' "${input}" > "${output}"
then
    : > "${output}"
    exit 1
fi

# Trim blank lines from both ends, strip a wrapping fence, then trim
# again -- a fence is normally flush against the markers but need not be.
trim_blank_lines() {
    sed -e '/./,$!d' -e ':a' -e '/^\n*$/{$d;N;ba' -e '}' "$1" > "$1.trimmed"
    mv "$1.trimmed" "$1"
}

trim_blank_lines "${output}"

first=$(head -1 "${output}")
last=$(tail -1 "${output}")
if [ "$(wc -l < "${output}")" -ge 2 ] && \
   [[ "${first}" =~ ^\`\`\`[a-zA-Z0-9_+-]*$ ]] && \
   [ "${last}" == '```' ]
then
    sed -i '1d;$d' "${output}"
    trim_blank_lines "${output}"
fi

# Whitespace-only is as useless to a reader as empty, and the caller
# falls back for both.
if ! grep -q '[^[:space:]]' "${output}"; then
    : > "${output}"
    exit 1
fi
