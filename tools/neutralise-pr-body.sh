#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# Neutralise the parts of a model-authored pull request description
# which GitHub acts on rather than merely renders.
#
#   neutralise-pr-body.sh <file>
#
# Rewrites <file> in place. Two constructs are defused:
#
# - An @mention notifies a real person the moment "gh pr create" runs,
#   which is before any human has looked at the draft, and it cannot be
#   taken back. The "@" is dropped, which is what the prompt asks the
#   model to do itself.
# - An issue-closing keyword ("fixes", "closes", "resolves" and their
#   inflections) followed by an issue reference closes that issue when
#   the pull request merges. The keyword is separated from the
#   reference so it reads as a citation instead. The workflow appends
#   its own "Fixes #NNNN" for the issue actually being fixed after this
#   pass, so that one is unaffected.
#
# The prompt already forbids both. This exists because prompt
# compliance is a weaker guarantee than code for a side effect which
# fires automatically and is not reversible -- the same argument this
# workflow makes for testing its marker parsing rather than trusting
# that the model emits well-formed blocks.
#
# Fenced code is passed through untouched: GitHub does not linkify
# inside a fence, so there is nothing to defuse, and a description
# quoting a decorator or an email address should survive intact. A
# fence left open at the end of the body is closed, so it cannot run on
# into the sections the workflow appends after it.

set -e

if [ $# -ne 1 ]; then
    echo "usage: $(basename "$0") <file>" >&2
    exit 2
fi

target="$1"

if [ ! -f "${target}" ]; then
    echo "neutralise-pr-body: no such file: ${target}" >&2
    exit 1
fi

awk '
    # awk has no backreferences in a replacement, so both rewrites walk
    # the line with match() and rebuild it rather than using gsub().

    # "Fixes #12" closes issue 12 when this merges. Separating the
    # keyword from the reference leaves a citation GitHub does not act
    # on. The reference may be bare, cross-repository, or a full URL.
    function defuse_closes(s,   out, matched, after) {
        out = ""
        while (match(s, /(^|[^[:alnum:]_])([Ff]ix(es|ed)?|[Cc]lose[sd]?|[Rr]esolve[sd]?)[[:space:]]+/)) {
            matched = substr(s, RSTART, RLENGTH)
            after = substr(s, RSTART + RLENGTH)
            if (after ~ /^(#[0-9]|[[:alnum:]._\/-]+#[0-9]|https?:\/\/[^[:space:]]*\/issues\/)/) {
                sub(/[[:space:]]+$/, " issue ", matched)
            }
            out = out substr(s, 1, RSTART - 1) matched
            s = after
        }
        return out s
    }

    # An @mention notifies the moment the pull request is created. The
    # character before it must not be one which would make this the
    # local part of an email address or part of a path.
    function defuse_mentions(s,   out, matched) {
        out = ""
        while (match(s, /(^|[^[:alnum:]_.+\/-])@[[:alnum:]][[:alnum:]-]*(\/[[:alnum:]._-]+)?/)) {
            matched = substr(s, RSTART, RLENGTH)
            sub(/@/, "", matched)
            out = out substr(s, 1, RSTART - 1) matched
            s = substr(s, RSTART + RLENGTH)
        }
        return out s
    }

    # A fence line is passed through and toggles protection for the
    # lines after it: GitHub does not linkify inside a fence, so there
    # is nothing to defuse and a quoted decorator should survive.
    /^[[:space:]]*```/ { infence = !infence; print; next }
    infence { print; next }
    { print defuse_mentions(defuse_closes($0)) }

    # A fence the model opened and never closed would otherwise run on
    # into the sections the workflow appends after this body, rendering
    # the diffstat and the footer as one preformatted lump.
    END { if (infence) { print "```" } }
' "${target}" > "${target}.neutralised"

mv "${target}.neutralised" "${target}"
