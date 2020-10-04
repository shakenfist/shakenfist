pipeline {
  agent {
    node {
      label 'sf-ci-image'
    }

  }
  stages {
    stage('Setup three nodes') {
      steps {
        node(label: 'sf-ci-image')
        node(label: 'sf-ci-image')
        node(label: 'sf-ci-image')
      }
    }

    stage('Log node names') {
      steps {
        sh 'echo "Hello world"'
      }
    }

  }
}