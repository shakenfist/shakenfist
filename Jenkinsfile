pipeline {
  agent {
    node {
      label 'sf-ci-image'
    }
  }

  stages {
    stage('Localhost deployment') {
      steps {
        sh '''  echo "Wait for apt to finish its on-boot work"
                sleep 60
                while [[ `ps -ef | grep -v grep | grep -c dpkg` -gt 0 ]]
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

                # We create a RAM disk for etcd to work around poor performance on cloud instances
                sudo mkdir -p /var/lib/etcd
                sudo mount -t tmpfs -o size=2g tmpfs /var/lib/etcd

                echo "Deploying on localhost"
                cd $WORKSPACE/deploy/ansible
                CLOUD=localhost RELEASE="git:master" ./deployandtest.sh
          '''
        }
      }
   stage('Run CI tests') {
     steps {
        sh '''  # Run the nextgen CI (the old CI wont work on single node deployments)
                cd $WORKSPACE/deploy
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
  }

  post {
    always {
      sh '''  echo "=============================="
              virsh list --all

              echo "=============================="
              pip list

              echo "=============================="
              cat /var/log/syslog'''
      }
    failure {
      sh '''  echo "Sleep for a long time in case we are debugging"
              sleep 3600
              '''
      }
    }
  }
