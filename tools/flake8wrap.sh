#!/bin/sh
#
# A simple wrapper around flake8 which makes it possible
# to ask it to only verify files changed in the current
# git HEAD patch.
#
# Intended to be invoked via tox:
#
#   tox -eflake8 -- -HEAD
#
# Originally from the OpenStack project.

FLAKE_COMMAND="flake8 --max-line-length=120"

if test "x$1" = "x-HEAD" ; then
    shift
    files=$(git diff --name-only HEAD~1 | tr '\n' ' ')
    echo "Running flake8 on ${files}"
    diff -u --from-file /dev/null ${files} | $FLAKE_COMMAND --diff "$@"
else
    echo "Running flake8 on all files"
    exec $FLAKE_COMMAND "$@"
fi
