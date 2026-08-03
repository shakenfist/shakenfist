#!/bin/bash

# Reconcile the pinned indirect dependency block in pyproject.toml with
# what the direct dependencies actually require.
#
# The block between the "# START_OF_INDIRECT_DEPS" and
# "# END_OF_INDIRECT_DEPS" marker comments is regenerated on every run, so
# pins for packages which are no longer required are removed as well as new
# requirements being added. The previous version of this process was
# append-only and accumulated stale pins forever. It works like this:
#
#   - The existing pinned versions are extracted and passed to the resolver
#     as pip *constraints*, not requirements. A constraint only applies if
#     something still requires the package, so still-needed packages
#     resolve to exactly their current pinned version (Renovate remains the
#     only thing that moves versions), while packages nothing requires any
#     more are simply not installed.
#   - The project dependencies are installed from a copy of pyproject.toml
#     with the pinned block stripped out, under those constraints. The
#     strip matters: a stale pin is otherwise itself a requirement which
#     forces its own installation, so it would never look stale.
#   - The block is then rewritten from pip freeze: every installed package
#     not already constrained elsewhere in pyproject.toml is recorded,
#     sorted case-insensitively.
#
# The "already constrained elsewhere" comparison uses PEP 503 canonical
# names ("-", "_" and "." are interchangeable), tolerates extras on direct
# pins (e.g. "gunicorn[gevent]==...") and accepts any version operator, not
# just "==". A direct dependency declared as a range (e.g. "psutil>=5.9.4")
# is still a declaration of intent about that package, so re-emitting it
# into the block as an exact pin would both duplicate it and override the
# deliberately loose bound. All of these mismatches have historically
# created duplicate pins which broke dependency resolution once Renovate
# bumped only one of the duplicates (see shakenfist#3398, shakenfist#3399
# and shakenfist#3462).
#
# The resolve runs once, in whatever environment invoked it -- in CI that
# is the lowest supported Python on Linux. Dependencies guarded by
# environment markers are therefore resolved for that environment only, so
# the block is complete for it but may omit packages needed only on a newer
# Python or another platform.
#
# Some packages must never be pinned even though they are installed. The
# canonical example is pydantic-core: pydantic pins it exactly (==), so an
# explicit pin can only agree with pydantic's pin or make the requirement
# set unsatisfiable, which happens whenever Renovate bumps one of the pair
# ahead of the other. Mark such packages with a comment anywhere in
# pyproject.toml, one package per line:
#
#     # never-pin: pydantic-core
#
# If the reconcile changed pyproject.toml and GITHUB_TOKEN is set, a
# branch is pushed and a pull request created. Without GITHUB_TOKEN the
# diff is just printed, which is useful for testing locally.
#
# Run this from the repository root. It works for both the application
# variant (pinned block in [project] dependencies) and the library variant
# (pinned block in the "pinned" extra of [project.optional-dependencies]).
#
# Template source:
#   https://github.com/shakenfist/development/tree/main/templates/pin-indirect-dependencies/

set -e

if [ ! -f pyproject.toml ]; then
    echo 'This script must be run from the repository root.' >&2
    exit 1
fi

for marker in START_OF_INDIRECT_DEPS END_OF_INDIRECT_DEPS; do
    if [ "$(grep -c "# ${marker}" pyproject.toml)" != '1' ]; then
        echo "pyproject.toml must contain exactly one # ${marker} marker." >&2
        exit 1
    fi
done

workdir=$(mktemp -d)
trap 'rm -rf "${workdir}"' EXIT

# The existing pins become constraints for the fresh resolve.
sed -n '/# START_OF_INDIRECT_DEPS/,/# END_OF_INDIRECT_DEPS/p' pyproject.toml \
    | sed -n 's/^ *"\([^"]*\)".*/\1/p' > "${workdir}/constraints.txt"

# A copy of pyproject.toml without the pinned block.
sed '/# START_OF_INDIRECT_DEPS/,/# END_OF_INDIRECT_DEPS/{/_OF_INDIRECT_DEPS/!d}' \
    pyproject.toml > "${workdir}/pyproject.toml"

# Packages explicitly marked as never to be pinned, as canonical names.
sed -n 's/^ *# never-pin: *//p' pyproject.toml \
    | sed -E 's/[-_.]+/-/g' | tr '[:upper:]' '[:lower:]' > "${workdir}/never_pin.txt"

# uv lives in a venv of its own and installs into the target venv from
# outside it. Installing uv into the target venv would put uv itself into
# the freeze output, which pins uv as a dependency of projects that do not
# require it -- and at whatever version pip chose, since a constraint on a
# package nothing requires does not apply.
python3 -m venv "${workdir}/uv"
"${workdir}/uv/bin/pip3" install uv

# The target venv is deliberately isolated (no --system-site-packages): if
# system packages could satisfy requirements then pip freeze would not see
# the complete dependency closure, and anything the system happened to
# provide would be wrongly dropped from the pinned block as stale.
python3 -m venv "${workdir}/venv"
if ! "${workdir}/uv/bin/uv" pip install --python "${workdir}/venv/bin/python" \
        -r "${workdir}/pyproject.toml" -c "${workdir}/constraints.txt"; then
    echo >&2
    echo 'The resolve failed with the existing pins applied as constraints.' >&2
    echo 'A direct dependency most likely now requires a transitive package' >&2
    echo 'at a version above its current pin, which the constraint forbids.' >&2
    echo 'The resolver output above names the conflicting pair. Let Renovate' >&2
    echo 'bump that pin, or bump it by hand in pyproject.toml, and re-run.' >&2
    echo >&2
    echo 'Until that is resolved no pins are reconciled, so obsolete pins' >&2
    echo 'will accumulate as well as new ones being missed.' >&2
    exit 1
fi

echo
echo 'Resolved dependencies:'
"${workdir}/venv/bin/pip3" freeze --local
echo

# Rebuild the pinned block from what was actually installed.
touch "${workdir}/pins.txt"
"${workdir}/venv/bin/pip3" freeze --local | while read -r depver; do
    case ${depver} in
        *==*) ;;
        *) continue ;;    # editable or direct-URL entries cannot be pinned
    esac

    dep=${depver%%==*}
    canon=$(echo "${dep}" | sed -E 's/[-_.]+/-/g' | tr '[:upper:]' '[:lower:]')
    if grep -qx "${canon}" "${workdir}/never_pin.txt"; then
        continue
    fi

    # Packaging machinery is never a runtime dependency of the project.
    # pip freeze happens to omit these today, but that exclusion list has
    # changed across pip releases and setuptools is declared in
    # [build-system] requires where the scan below will not see it, so a
    # newer pip in a rebuilt runner image could otherwise silently pin a
    # build-time package as a runtime one.
    case ${canon} in
        pip|setuptools|wheel|distribute) continue ;;
    esac

    depre=$(echo "${dep}" | sed -E 's/[-_.]+/[-_.]/g')
    if [ "$(grep -Eic "\"${depre}(\[[a-z0-9,_.-]+\])?(==|>=|<=|~=|!=|===|>|<)" "${workdir}/pyproject.toml")" -lt 1 ]; then
        echo "${depver}" >> "${workdir}/pins.txt"
    fi
done

# The collation is pinned because glibc's locale-aware ordering ignores "-"
# and "_" in its first pass, so an unpinned sort orders the block
# differently on a UTF-8 workstation than on the C/POSIX CI runner. That
# turns a local dry run into a diff made entirely of reordering noise.
LC_ALL=C sort -f "${workdir}/pins.txt" > "${workdir}/pins_sorted.txt"

awk -v pins="${workdir}/pins_sorted.txt" '
    /# START_OF_INDIRECT_DEPS/ {
        print
        while ((getline depver < pins) > 0) {
            print "    \"" depver "\","
        }
        close(pins)
        skipping = 1
        next
    }
    /# END_OF_INDIRECT_DEPS/ { skipping = 0 }
    skipping != 1 { print }
' pyproject.toml > "${workdir}/pyproject_updated.toml"
cp "${workdir}/pyproject_updated.toml" pyproject.toml

# Distinguish "no change" from "git could not tell us". Piping git diff into
# wc conflates the two, because the pipeline exits with wc's status: a git
# failure then reads as a clean tree and the reconcile is silently dropped.
rc=0
git diff --quiet || rc=$?
if [ "${rc}" = '0' ]; then
    echo 'Pinned indirect dependencies are already up to date.'
    exit 0
elif [ "${rc}" != '1' ]; then
    echo 'git diff failed, so whether the pins changed is unknown.' >&2
    exit 1
fi

echo 'Pinned indirect dependencies changed:'
echo
git diff

if [ -z "${GITHUB_TOKEN}" ]; then
    echo
    echo 'GITHUB_TOKEN is not set, so not creating a pull request.'
    exit 0
fi

# A pull_request checkout is a detached HEAD at the merge commit, so a
# branch cut from here would carry the whole pull request into what is
# meant to be a pin-only update. The workflow withholds GITHUB_TOKEN on
# pull_request events so this should be unreachable, but the consequence
# of getting it wrong is a bogus automated pull request, so check anyway.
case ${GITHUB_REF} in
    refs/pull/*)
        echo >&2
        echo 'Refusing to push: this is a pull request merge ref, so the' >&2
        echo 'branch would contain the pull request rather than just the' >&2
        echo 'reconciled pins. Unset GITHUB_TOKEN to take the diff-only path.' >&2
        exit 1
        ;;
esac

datestamp=$(date '+%Y%m%d')
git checkout -b "pin-dependencies-${datestamp}"

git config --global user.name 'shakenfist-bot'
git config --global user.email 'bot@shakenfist.com'
git commit -a -m 'Update pinned dependencies.'
git push -f origin "pin-dependencies-${datestamp}"
echo

# Ensure the label exists before creating the PR
gh label create dependencies --color 0075ca \
    --description 'Pull requests that update a dependency file' \
    2>/dev/null || true

# A second run on the same UTC day force-pushes onto the existing branch,
# which correctly updates the open pull request. gh pr create would then
# exit non-zero because one already exists, failing a job which had in
# fact done its work.
if gh pr view "pin-dependencies-${datestamp}" >/dev/null 2>&1; then
    echo 'Existing pull request updated.'
    exit 0
fi

gh pr create \
    --assignee mikalstill \
    --reviewer mikalstill \
    --title 'Update pinned dependencies.' \
    --body 'Indirect dependency pins were reconciled against the current direct dependencies. Additions are new transitive requirements; removals are pins nothing requires any more.' \
    --label dependencies
echo
echo 'Pull request created.'
