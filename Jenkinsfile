pipeline {
  agent {
    node {
      label 'sf-ci-image'
    }
  }

  stages {
    stage('Localhost deployment') {
      steps {
        sh '''  echo "Updating worker"
                sudo apt-get update
                sudo apt-get -y dist-upgrade
                sudo apt-get -y install tox ansible pwgen build-essential python3-dev python3-wheel python3-pip curl
                ansible-galaxy install andrewrothstein.etcd-cluster andrewrothstein.terraform andrewrothstein.go

                echo "Deploying on localhost"
                cd deploy/ansible

                # This is a terrible hack to get around https://github.com/shakenfist/deploy/issues/75
                CLOUD=localhost RELEASE="git:master" ./deployandtest.sh || true
                CLOUD=localhost RELEASE="git:master" ./deployandtest.sh

                # Run the nextgen CI (the old CI wont work on single node deployments)
                cd ..
                sudo chmod ugo+rx /etc/sf/shakenfist.json
                tox -epy3
        '''
      }
    }
  }

  post {
    always {
      sh '''  # Make /var/log/syslog archivable
              ln -s /var/log/syslog syslog
        '''
      archiveArtifacts artifacts: 'syslog', followSymlinks: false
      }
    }
  }
