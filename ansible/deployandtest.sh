#!/bin/bash -ex

tox -epy37

cd tf
terraform destroy -auto-approve -var project=mikal-269605
cd ..

ansible-playbook -i hosts-gcp --extra-vars "project=mikal-269605" deploy.yml
ansible-playbook -i hosts-gcp --extra-vars "project=mikal-269605" test.yml
