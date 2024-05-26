#!/bin/bash

# $1 is the minimum python version, as a small string. For example "36".

datestamp=$(date "+%Y%m%d")
git checkout -b formatting-automations

# We only want to change five files at a time
changed=0
for file in $( find . -type f -name "*.py" | egrep -v "(_pb2.py|pb2_grpc.py)"); do
    out=$( ${RUNNER_TEMP}/venv/bin/pyupgrade --py${1}-plus --exit-zero-even-if-changed ${file} 2>&1 || true )
    rewrites=$( echo ${out} | grep -c "Rewriting" || true )
    if [ ${rewrites} -gt 0 ]; then
        echo "${file} was modified"
    fi
    changed=$(( ${changed} + $rewrites ))

    if [ ${changed} -gt 4 ]; then
        break
    fi
done

# Did we find something new?
if [ $(git diff | wc -l) -gt 0 ]; then
echo "Code change detected..."
echo
git diff

git config --global user.name "shakenfist-bot"
git config --global user.email "bot@shakenfist.com"
git commit -a -m "Automated code formatting for ${datestamp}."
git push -f origin formatting-automations
echo
gh pr create \
    --assignee mikalstill \
    --reviewer mikalstill \
    --title "Automated code formatting for ${datestamp}." \
    --body "Automated code formatting."
echo
echo "Pull request created."
fi