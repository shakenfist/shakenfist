#!/bin/bash -ex

cwd=`pwd`
TERRAFORM_VARS=""
ANSIBLE_VARS="cloud=$CLOUD bootdelay=$BOOTDELAY ansible_root=$cwd"
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
  cd $cwd
fi

ansible-playbook -i hosts --extra-vars "$ANSIBLE_VARS" deploy.yml

if [ "%$SKIP_SF_TESTS%" == "%%" ]
then
  for playbook in `ls tests/test_*.yml | grep -v test_final.yml | shuf`
  do
    ansible-playbook -i hosts --extra-vars "$ANSIBLE_VARS" $playbook
  done
fi

ansible-playbook -i hosts --extra-vars "$ANSIBLE_VARS" tests/test_final.yml
