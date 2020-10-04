pipeline {
  agent {
    node {
      label 'sf-ci-image'
    }

  }
  stages {
    stage('Simple hello world') {
      steps {
        sh 'echo "Hello world"'
      }
    }

  }
}
