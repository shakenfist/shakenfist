pipeline {
  agent {
    node {
      label 'sf-privci'
    }
  }

  stages {
    stage('Dependencies') {
      steps {
        sh '''  echo "Wait for apt to finish its on-boot work"
                sleep 60
                while [[ `ps -ef | grep -v grep | grep -cE '(apt|dpkg)'` -gt 0 ]]
                do
                  echo "Waiting for apt to finish..."
                  sleep 10
                done

                echo "Updating worker"
                sudo apt-get update
                sudo apt-get -y dist-upgrade
                sudo apt-get -y install pwgen build-essential python3-dev python3-wheel python3-pip curl

                sudo pip3 install -U ansible tox
                ansible-galaxy install andrewrothstein.etcd-cluster andrewrothstein.terraform andrewrothstein.go
                ansible-galaxy collection install community.general

                # We create a RAM disk for etcd to work around poor performance on cloud instances
                sudo mkdir -p /var/lib/etcd
                sudo mount -t tmpfs -o size=2g tmpfs /var/lib/etcd
          '''
        }
      }
    stage('Install pypi release') {
      steps {
        sh '''

                if [ '%%' == '%%' ]
                then
                  BRANCH=`git rev-parse --abbrev-ref HEAD`
                else
                  BRANCH=""
                fi

                cd $WORKSPACE/deploy/ansible



                # Install the latest 0.3 pypi release
                export CLOUD="localhost"
                export FLOATING_IP_BLOCK="10.0.0.0/24"
                export KSM_ENABLED="0"
                export RELEASE=`curl -s https://pypi.org/simple/shakenfist/ | grep whl | sed -e 's/.*shakenfist-//' -e 's/-py3.*//' | grep 0.3 | tail -1`
                ./deployandtest.sh

                # Log what versions we are running
                echo "============================================================"
                pip list | grep shakenfist
                echo "============================================================"
          '''
        }
      }
    stage('Create sample infrastructure') {
      steps {
        sh '''  # Start an instance to upgrade
                sudo chmod ugo+r /etc/sf/sfrc
                . /etc/sf/sfrc
                sf-client namespace create upgrade

                fe=`sf-client --simple network create upgrade-fe 192.168.20.0/24 --namespace upgrade | grep uuid | cut -f 2 -d ":"`
                be=`sf-client --simple network create upgrade-be 192.168.30.0/24 --namespace upgrade | grep uuid | cut -f 2 -d ":"`

                ud=`echo "#!/bin/sh
  sudo echo 'auto lo'                >  /etc/network/interfaces
  sudo echo 'iface lo inet loopback' >> /etc/network/interfaces
  sudo echo ''                       >> /etc/network/interfaces
  sudo echo 'auto eth0'              >> /etc/network/interfaces
  sudo echo 'iface eth0 inet dhcp'   >> /etc/network/interfaces
  sudo echo ''                       >> /etc/network/interfaces
  sudo echo 'auto eth1'              >> /etc/network/interfaces
  sudo echo 'iface eth1 inet dhcp'   >> /etc/network/interfaces
  sudo /etc/init.d/S40network restart
  " | base64`

                sf-client instance create fe 1 1024 -n $fe -N network_uuid=$be,address=192.168.30.10 -d 8@cirros --namespace upgrade --encodeduserdata "$ud"
                sf-client instance create be-1 1 1024 -N network_uuid=$be,address=192.168.30.50 -d 8@cirros --namespace upgrade
                sf-client instance create be-2 1 1024 -N network_uuid=$be,address=192.168.30.51 -d 8@cirros --namespace upgrade
          '''
        }
      }
    stage('Pre-upgrade log check') {
      steps {
        sh '''  # Ensure we don't have any tracebacks
                if [ `grep -c "Traceback (most recent call last):" /var/log/syslog` -gt 0 ]
                then
                  echo "We have tracebacks in the logs!"
                  exit 1
                fi

                # Ensure we didn't log any errors
                if [ `grep -c "ERROR"` -gt 0 ]
                then
                  echo "Errors were logged!"
                  exit 1
                fi

                # Ensure nothing died
                if [ `grep -c "died"` -gt 0 ]
                then
                  echo "A process died!"
                  exit 1
                fi
          '''
      }
    }
    stage('Pre-upgrade process check') {
      steps {
        sh '''  # No zombies!
                if [ `ps -ef | grep sf | grep -c defunct` -gt 0 ]
                then
                  echo "We have zombies!"
                  exit 1
                fi
          '''
      }
    }
    stage('Install test version') {
      steps {
        sh '''


                if [ '%%' == '%%' ]
                then
                  BRANCH=`git rev-parse --abbrev-ref HEAD`
                else
                  BRANCH=""
                fi

                cd $WORKSPACE/deploy/ansible
                export RELEASE="git:master"

                export CLOUD="localhost"
                export FLOATING_IP_BLOCK="10.0.0.0/24"
                export KSM_ENABLED="0"

                echo "Deploying $RELEASE to cloud $CLOUD"
                ./deployandtest.sh

                # Log what versions we are running
                echo "============================================================"
                pip list | grep shakenfist
                echo "============================================================"
          '''
        }
      }
    stage('Re-run upgrade to make sure that its a noop') {
      steps {
        sh '''  sf-upgrade'''
      }
    }
    stage('Run CI tests') {
      steps {
        sh '''  # Run the nextgen CI
                cd $WORKSPACE/deploy/
                sudo chmod ugo+rx /etc/sf/shakenfist.json
                tox -epy3
          '''
      }
    }
    stage('Log check') {
      steps {
        sh '''  # Ensure we don't have any tracebacks
                if [ `grep -c "Traceback (most recent call last):" /var/log/syslog` -gt 0 ]
                then
                  echo "We have tracebacks in the logs!"
                  exit 1
                fi

                # Ensure we didn't log any errors
                if [ `grep -c "ERROR"` -gt 0 ]
                then
                  echo "Errors were logged!"
                  exit 1
                fi

                # Ensure nothing died
                if [ `grep -c "died"` -gt 0 ]
                then
                  echo "A process died!"
                  exit 1
                fi
          '''
      }
    }
    stage('Process check') {
      steps {
        sh '''  # No zombies!
                if [ `ps -ef | grep sf | grep -c defunct` -gt 0 ]
                then
                  echo "We have zombies!"
                  exit 1
                fi
          '''
      }
    }
  }

  post {
    always {
      sh '''  echo "=============================="
              virsh list --all
              '''

      sh '''  echo "=============================="
              pip list

              echo "=============================="
              cat /var/log/syslog
              '''
      sh '''  echo "=============================="
              etcdctl get --prefix /sf
              '''
      }
    failure {
      sh '''  echo "Sleep for a long time in case we are debugging"
              echo "Create /keepme to extend the reboot time - up to 12 hours"
              x=12
              while [ $x -gt 0 ]
              do
                  echo Sleeping...
                  sleep 3600
                  x=$(( $x - 1 ))
                  if [ ! -f /keep ]; then
                      x=0
                  fi
              done
              '''
      }
    }
}