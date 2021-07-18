#!/bin/bash -e
#
# ./deployandtest.sh [aws|aws-single-node|gcp|metal|openstack|shakenfist]

#### Required settings
VARIABLES=""
VERBOSE="-v"

#### Metal
if [ "$CLOUD" == "metal" ]
then
  if [ -z "$METAL_IP_SF1" ]
  then
    echo ===== Must specify the Node 1 machine IP in \$METAL_IP_SF1
    exit 1
  fi
  VARIABLES="$VARIABLES,metal_ip_sf1=$METAL_IP_SF1"

  if [ -z "$METAL_IP_SF2" ]
  then
    echo ===== Must specify the Node 2 machine IP in \$METAL_IP_SF2
    exit 1
  fi
  VARIABLES="$VARIABLES,metal_ip_sf2=$METAL_IP_SF2"

  if [ -z "$METAL_IP_SF3" ]
  then
    echo ===== Must specify the Node 3 machine IP in \$METAL_IP_SF3
    exit 1
  fi
  VARIABLES="$VARIABLES,metal_ip_sf3=$METAL_IP_SF3"

  if [ -n "$METAL_SSH_KEY_FILENAME" ]
  then
    d=`cat $METAL_SSH_KEY_FILENAME`
    VARIABLES="$VARIABLES,ssh_key_filename=$METAL_SSH_KEY_FILENAME"
    VARIABLES="$VARIABLES,ssh_key=\"$d\",ssh_user=$METAL_SSH_USER"
  else
    VARIABLES="$VARIABLES,ssh_key_filename=\"\",ssh_key=\"\",ssh_user=\"\""
  fi
fi

#### Localhost
if [ "$CLOUD" == "localhost" ]
then
  VARIABLES="$VARIABLES,ram_system_reservation=1.0"
  IGNORE_MTU="1"
fi

#### Shakenfist
if [ "$CLOUD" == "shakenfist" ]
then
  if [ -z "$SHAKENFIST_KEY" ]
  then
    echo ===== Must specify the Shaken Fist system key to use in \$SHAKENFIST_KEY
    exit 1
  fi
  VARIABLES="$VARIABLES,system_key=$SHAKENFIST_KEY"

  if [ -z "$SHAKENFIST_SSH_KEY" ]
  then
    echo ===== Must specify a SSH public key\'s text in \$SHAKENFIST_SSH_KEY
  fi
  VARIABLES="$VARIABLES,ssh_key=\"$SHAKENFIST_SSH_KEY\""
fi

#### Check that a valid cloud was specified
if [ -z "$CLOUD" ]
then
{
  echo ====
  echo ==== CLOUD should be specified: localhost, metal, shakenfist
  echo ====
  echo ==== Continuing, because you might know what you are doing...
  echo
} 2> /dev/null
fi

#### Configure system/admin key from Vault key path or specified password
if [ -n "$VAULT_SYSTEM_KEY_PATH" ]
then
  if [ -n "$ADMIN_PASSWORD" ]
  then
    echo ===== Specify either ADMIN_PASSWORD or VAULT_SYSTEM_KEY_PATH \(not both\)
    exit 1
  fi

  VARIABLES="$VARIABLES,vault_system_key_path=$VAULT_SYSTEM_KEY_PATH"
fi

set -x

#### Default settings
BOOTDELAY="${BOOTDELAY:-2}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-Ukoh5vie}"
FLOATING_IP_BLOCK="${FLOATING_IP_BLOCK:-10.10.0.0/24}"
UNIQIFIER="${UNIQIFIER:-$USER"-"`date "+%y%m%d"`"-"`pwgen --no-capitalize -n1`"-"}"
KSM_ENABLED="${KSM_ENABLED:-1}"
GLUSTER_ENABLED="${GLUSTER_ENABLED:-0}"
GLUSTER_REPLICAS="${GLUSTER_REPLICAS:-0}"
DEPLOY_NAME="sf"
RESTORE_BACKUP="${RESTORE_BACKUP:-}"
IGNORE_MTU="${IGNORE_MTU:-0}"
DNS_SERVER="${DNS_SERVER:-8.8.8.8}"
HTTP_PROXY="${HTTP_PROXY:-}"
INCLUDE_TRACEBACKS="${INCLUDE_TRACEBACKS:-False}"

# Setup variables for consumption by ansible and terraform
cwd=`pwd`

VARIABLES="$VARIABLES,cloud=$CLOUD"
VARIABLES="$VARIABLES,bootdelay=$BOOTDELAY"
VARIABLES="$VARIABLES,uniqifier=$UNIQIFIER"
VARIABLES="$VARIABLES,admin_password=$ADMIN_PASSWORD"
VARIABLES="$VARIABLES,floating_network_ipblock=$FLOATING_IP_BLOCK"
VARIABLES="$VARIABLES,ksm_enabled=$KSM_ENABLED"
VARIABLES="$VARIABLES,gluster_enabled=$GLUSTER_ENABLED"
VARIABLES="$VARIABLES,gluster_replicas=$GLUSTER_REPLICAS"
VARIABLES="$VARIABLES,deploy_name=$DEPLOY_NAME"
VARIABLES="$VARIABLES,restore_backup=\"$RESTORE_BACKUP\""
VARIABLES="$VARIABLES,ignore_mtu=\"$IGNORE_MTU\""
VARIABLES="$VARIABLES,dns_server=\"$DNS_SERVER\""
VARIABLES="$VARIABLES,http_proxy=\"$HTTP_PROXY\""
VARIABLES="$VARIABLES,include_tracebacks=\"$INCLUDE_TRACEBACKS\""

echo "VARIABLES: $VARIABLES"
ANSIBLE_VARS=""

VARIABLES=`echo $VARIABLES | sed 's/^,//'`

OLDIFS=$IFS
IFS=,
vars=($(echo "$VARIABLES"))
IFS=$OLDIFS

mkdir -p /etc/sf/
echo "# Install started at "`date` > /etc/sf/deploy-variables
for var in "${vars[@]}"
do
  echo "#    $var" >> /etc/sf/deploy-variables
  ANSIBLE_VARS="$ANSIBLE_VARS $var"
done

encoded=`echo $ANSIBLE_VARS | base64 -w 0`

echo "" >> /etc/sf/deploy-variables
echo "export CLOUD=$CLOUD" >> /etc/sf/deploy-variables
echo "export ENCODED_ANSIBLE_VARS=$encoded" >> /etc/sf/deploy-variables
echo 'export ANSIBLE_VARS=`echo $ENCODED_ANSIBLE_VARS | base64 -d`' >> /etc/sf/deploy-variables

ANSIBLE_SSH_PIPELINING=0 ansible-playbook $VERBOSE -i hosts --extra-vars "$ANSIBLE_VARS ansible_root=\"$cwd\"" deploy.yml

echo "" >> /etc/sf/deploy-variables
echo "# Install finished at "`date` >> /etc/sf/deploy-variables
