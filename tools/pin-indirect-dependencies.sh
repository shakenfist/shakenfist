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
#     not already exactly pinned ("name==...") elsewhere in pyproject.toml
#     is recorded, sorted case-insensitively.
#
# The "already pinned elsewhere" comparison uses PEP 503 canonical names
# ("-", "_" and "." are interchangeable) and tolerates extras on direct
# pins (e.g. "gunicorn[gevent]==..."). Both of those mismatches have
# historically created duplicate pins which broke dependency resolution
# once Renovate bumped only one of the duplicates (see shakenfist#3398,
# shakenfist#3399 and shakenfist#3462).
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
# Template source: shakenfist/development/templates/pin-indirect-dependencies/

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
"${workdir}/uv/bin/uv" pip install --python "${workdir}/venv/bin/python" \
    -r "${workdir}/pyproject.toml" -c "${workdir}/constraints.txt"

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

    depre=$(echo "${dep}" | sed -E 's/[-_.]+/[-_.]/g')
    if [ "$(grep -Eic "\"${depre}(\[[a-z0-9,_.-]+\])?==" "${workdir}/pyproject.toml")" -lt 1 ]; then
        echo "${depver}" >> "${workdir}/pins.txt"
    fi
done
sort -f "${workdir}/pins.txt" > "${workdir}/pins_sorted.txt"

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

gh pr create \
    --assignee mikalstill \
    --reviewer mikalstill \
    --title 'Update pinned dependencies.' \
    --body 'Indirect dependency pins were reconciled against the current direct dependencies. Additions are new transitive requirements; removals are pins nothing requires any more.' \
    --label dependencies
echo
echo 'Pull request created.'
