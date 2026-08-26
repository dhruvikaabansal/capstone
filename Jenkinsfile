// CI/CD pipeline for the Smart Policing Portal.
//
// Works on both Windows and Unix agents: every shell step goes through run(),
// which picks `bat` or `sh` based on the agent it lands on.

def run(String command) {
    if (isUnix()) {
        sh command
    } else {
        bat command
    }
}

pipeline {
    agent any

    options {
        timeout(time: 30, unit: 'MINUTES')
        timestamps()
        disableConcurrentBuilds()
    }

    environment {
        SESSION_SECRET       = credentials('spp-session-secret')
        GOOGLE_CLIENT_ID     = credentials('google-client-id')
        GOOGLE_CLIENT_SECRET = credentials('google-client-secret')
        APP_URL              = 'http://localhost:3001'
        MODEL_URL            = 'http://localhost:8000'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Install') {
            parallel {
                stage('Node dependencies') {
                    steps {
                        run 'npm ci'
                    }
                }
                stage('Python dependencies') {
                    steps {
                        dir('ml-service') {
                            run 'python -m pip install --upgrade pip'
                            run 'python -m pip install -r requirements.txt'
                        }
                    }
                }
            }
        }

        stage('Test') {
            parallel {
                stage('Portal tests') {
                    steps {
                        run 'npm test'
                    }
                }
                stage('Prediction service tests') {
                    steps {
                        dir('ml-service') {
                            run 'python -m pytest tests -q'
                        }
                    }
                }
            }
        }

        stage('Build images') {
            steps {
                run 'docker compose build'
            }
        }

        stage('Deploy') {
            steps {
                run 'docker compose down --remove-orphans'
                run 'docker compose up -d'
            }
        }

        stage('Smoke test') {
            steps {
                // Compose waits on the container health checks, but the curl
                // retries cover the gap between "container healthy" and
                // "route serving".
                run "curl --retry 20 --retry-delay 5 --retry-all-errors -fsS ${MODEL_URL}/health"
                run "curl --retry 20 --retry-delay 5 --retry-all-errors -fsS ${APP_URL}/health"
                run "curl -fsS ${MODEL_URL}/stats"
                run "curl -fsS -X POST ${MODEL_URL}/predict -H \"Content-Type: application/json\" -d \"{\\\"lat\\\":41.885,\\\"lon\\\":-87.63}\""
            }
        }
    }

    post {
        failure {
            echo 'Pipeline failed. Container logs follow.'
            script {
                try {
                    run 'docker compose logs --tail 100'
                } catch (err) {
                    echo "Could not collect logs: ${err}"
                }
            }
        }
        success {
            echo "Deployed. Portal: ${APP_URL}  Prediction service: ${MODEL_URL}/docs"
        }
    }
}
