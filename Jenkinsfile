pipeline {
  agent {
    node {
      label 'sf-privci'
    }
  }

  stages {
    stage('Setup') {
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

                if [ '%%' == '%%' ]
                then
                  BRANCH=`git rev-parse --abbrev-ref HEAD`
                else
                  BRANCH=""
                fi

                # This means we'll use the master branch of our other repos
                export RELEASE="git:master"
                cd $WORKSPACE/deploy/ansible

                # We restore a backup to ensure upgrades work
                export RESTORE_BACKUP="$WORKSPACE/examples/schema-v0_3_3.tgz"

                # The actual job
                export CLOUD="localhost"
                export FLOATING_IP_BLOCK="10.0.0.0/24"
                export KSM_ENABLED="0"

                echo "Deploying $RELEASE to cloud $CLOUD"
                ./deployandtest.sh
          '''
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
    stage('Assert that the logs look sensible') {
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
    stage('Assert that we have a reasonable set of processes') {
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