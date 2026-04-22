pipeline {
    agent any
    environment {
        GOOGLE_CLIENT_ID = credentials('google-client-id')
        GOOGLE_CLIENT_SECRET = credentials('google-client-secret')
    }
    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }
        stage('Setup Environment') {
            steps {
                bat 'npm install'
            }
        }
        stage('Build & Deploy') {
            steps {
                bat 'docker-compose down || exit 0'
                bat 'docker-compose up -d --build'
            }
        }
        stage('Health Check') {
            steps {
                bat 'curl --retry 12 --retry-delay 5 --retry-all-errors -f http://localhost:3001/health || exit 1'
                bat 'curl --retry 12 --retry-delay 5 --retry-all-errors -f http://localhost:8000/health || exit 1'
            }
        }
    }
    post {
        success {
            echo 'Pipeline completed successfully!'
        }
        failure {
            echo 'Pipeline failed!'
        }
    }
}