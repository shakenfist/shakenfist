#!/bin/bash -ex

tox -epy37


TERRAFORM_VARS=""
ANSIBLE_VARS="cloud=$CLOUD bootdelay=$BOOTDELAY"
for var in $VARIABLES
do
  TERRAFORM_VARS="$TERRAFORM_VARS -var=$var"
  ANSIBLE_VARS="$ANSIBLE_VARS $var"
done


# Nutanix is a special case
if [ "$CLOUD" == "nutanix" ]
then
  cd terraform/nutanix/phase-2
  terraform destroy -auto-approve $TERRAFORM_VARS -state=../terraform.tfstate
  cd ..
  find . -type f -name "*tfstate*" -exec rm {} \;
  cd ../..
else
  cd terraform/$CLOUD
  terraform destroy -auto-approve $TERRAFORM_VARS
  cd ../..
fi

ansible-playbook -i hosts --extra-vars "$ANSIBLE_VARS" deploy.yml

if [ "%$SKIP_SF_TESTS%" == "%%" ]
then
  time ansible-playbook -i hosts --extra-vars "$ANSIBLE_VARS" test.yml
fi
