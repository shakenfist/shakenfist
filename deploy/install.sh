#!/bin/bash

# A simple script to extract the installer and run it.

CWD=`pwd`
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

mkdir -p /tmp/shakenfist_install_$$
cd /tmp/shakenfist_install_$$
tar xzf $DIR/deploy.tgz --directory .
cd deploy/ansible/
./deploy.sh

cd "$CWD"
rm -rf /tmp/shakenfist_install_$$

