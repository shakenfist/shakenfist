#!/bin/bash -e

# Release a specified version of Shaken Fist

echo "--- Determine verison number ---"
PREVIOUS=`git tag | egrep "^v" | sort -n | tail -1 | sed 's/^v//'`

echo
echo -n "What is the version number (previous was $PREVIOUS)? "
read VERSION

echo
echo "--- Preparing deployer ---"
set -x
rm -rf deploy/shakenfist_ci.egg-info deploy/gitrepos deploy/.tox
find deploy/ansible/terraform -type f -name "*tfstate*" -exec rm {} \;
find deploy/ansible/terraform -type d  -name ".terraform" -exec rm -rf {} \;

rm -f deploy.tgz
tar cvzf deploy.tgz deploy

rm -f docs.tgz
tar cvzf docs.tgz docs
set +x

echo
echo "--- Building ---"
set -x
pip install --upgrade readme-renderer
pip install --upgrade twine
rm -rf build dist *.egg-info
git pull
python3 setup.py sdist bdist_wheel
twine check dist/*
set +x

echo
echo "--- Uploading ---"
echo "This is the point where we push files to pypi. Hit ctrl-c to abort."
read DUMMY

set -x
git tag -s "v$VERSION" -m "Release v$VERSION"
git push origin "v$VERSION"
twine upload dist/*
set +x