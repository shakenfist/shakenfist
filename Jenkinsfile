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
                sudo apt-get -y install ansible tox pwgen build-essential python3-dev python3-wheel curl
                ansible-galaxy install andrewrothstein.etcd-cluster andrewrothstein.terraform andrewrothstein.go

                echo "Deploying on localhost"
                cd deploy/ansible
                CLOUD=localhost RELEASE="git:master" ./deployandtest.sh
        '''
      }
    }

  }
}
