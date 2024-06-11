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
    files=$(git diff --name-only HEAD~1 | grep -v _pb2 | egrep ".py$")
    if [ -z "${files}" ]; then
        echo "No python files in change."
        exit 0
    fi

    filtered_files=""
    for file in $files; do
        if [ -e "$file" ]; then
            filtered_files="${filtered_files} ${file}"
        else
            echo "$file does not exist in the end state, skipping."
        fi
    done

    echo "Running flake8 on ${filtered_files}"
    diff -u --from-file /dev/null ${filtered_files} | $FLAKE_COMMAND ${filtered_files}
else
    echo "Running flake8 on all files"
    exec $FLAKE_COMMAND "$@"
fi
