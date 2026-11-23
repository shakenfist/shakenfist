#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# Count the non-test, non-generated lines a fix changes.
#
#   measure-change-size.sh <base commit> <test path pattern>
#
# Prints the number of lines added plus lines removed between <base
# commit> and the index, leaving out files whose path matches <test
# path pattern> (an awk extended regular expression) and files which
# <base commit>'s .gitattributes marks linguist-generated. Run it from
# inside the repository after "git add -A". Any failure exits non-zero
# with nothing on stdout, never a count.
#
# The rules below each exist because the obvious version got one of
# them wrong:
#
# - This is a safety cap, so it must fail closed. Inline, the
#   measurement was "git diff | awk", and without pipefail a failing
#   git left awk to print 0 for its empty input: the fix measured as
#   no change at all and was published however large it was.
# - The base is a commit recorded before the model ran, not HEAD. The
#   prompt tells the model not to commit, but that is a request, and a
#   commit would move HEAD past the work it is meant to measure.
# - The attributes come from the base too (--attr-source), not the
#   working tree or the index, so a fix cannot mark its own code
#   generated to slip under the cap.
# - Both spellings of the attribute count: "path linguist-generated"
#   sets it, but "path linguist-generated=true" -- the form GitHub's
#   linguist documentation uses -- gives it the string value "true",
#   which the pathspec magic "attr:linguist-generated" does not match.
#   "linguist-generated=false" is linguist's opt-out and is still
#   counted.
# - --attr-source needs git 2.41 or later. Older git counts generated
#   files with a warning, which is the conservative direction: it can
#   hold a fix back, but never let an oversized one through.
# - numstat is tab separated, and a path may contain spaces. Binary
#   files report "-" for both counts and are not counted.

set -euo pipefail

if [ $# -ne 2 ]; then
    echo "usage: measure-change-size.sh <base commit> <test path pattern>" >&2
    exit 2
fi

base="$1"
pattern="$2"

if ! git rev-parse --verify --quiet "${base}^{commit}" > /dev/null; then
    echo "measure-change-size: not a commit: ${base}" >&2
    exit 2
fi

attr_source=(--attr-source="${base}")
exclude=(':(exclude,attr:linguist-generated)'
         ':(exclude,attr:linguist-generated=true)')
if ! git --attr-source="${base}" version > /dev/null 2>&1; then
    echo "::warning::$(git version) cannot read attributes from a commit" \
         "(git 2.41 or later is needed), so files marked" \
         "linguist-generated are counted against the cap." >&2
    attr_source=()
    exclude=()
fi

numstat=$(git "${attr_source[@]}" diff --cached --numstat "${base}" \
    -- ':/' "${exclude[@]}")

printf '%s\n' "${numstat}" | awk -F '\t' -v pat="${pattern}" '
    NF >= 3 && $3 !~ pat { if ($1 != "-") s += $1 + $2 }
    END { print s + 0 }'
