#!/bin/bash

# Run all the tests 10 times in a random order to see if we can
# shake anything out.

tox -epy37


TERRAFORM_VARS=""
ANSIBLE_VARS="cloud=$CLOUD bootdelay=$BOOTDELAY"
for var in $VARIABLES
do
  TERRAFORM_VARS="$TERRAFORM_VARS -var=$var"
  ANSIBLE_VARS="$ANSIBLE_VARS $var"
done

for i in `seq 10`
do
  for playbook in `ls tests/test_*.yml | grep -v test_final.yml | shuf`
  do
    ansible-playbook -i hosts --extra-vars "$ANSIBLE_VARS" $playbook
    if [ $? -gt 0 ]
    then
      echo
      echo "TESTS FAILED!"
      echo "  Test: $playbook"
      echo "  Iteration: $i"
      exit 1
    fi
  done
done

ansible-playbook -i hosts --extra-vars "$ANSIBLE_VARS" tests/test_final.yml
