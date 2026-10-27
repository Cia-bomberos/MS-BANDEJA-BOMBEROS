pipeline {
    agent any

    options {
        buildDiscarder(logRotator(numToKeepStr: '1', artifactNumToKeepStr: '1'))
        disableConcurrentBuilds()
        timestamps()
        skipDefaultCheckout(true)
    }

    // Datos de la BD propia del equipo para aplicar migraciones (opcionales:
    // sin DB_HOST el stage de migraciones se omite). El deploy ya no corre
    // en este pipeline.
    parameters {
        string(name: 'DB_HOST', defaultValue: '', description: 'Endpoint del RDS propio del equipo')
        string(name: 'DB_NAME', defaultValue: '', description: 'Nombre de la BD del stage a desplegar (dev/qa/uat/prod)')
        string(name: 'DB_USER', defaultValue: '', description: 'Usuario de conexión al RDS')
        password(name: 'DB_PASSWORD', defaultValue: '', description: 'Password de conexión al RDS')
    }

    stages {
        stage('Checkout Repo') {
            steps {
                checkout scm
            }
        }

        stage('Test & Coverage (Python)') {
            agent {
                docker {
                    // Misma versión que el runtime real de Lambda (python3.11
                    // en serverless.yml), para no repetir el desfase que
                    // tenía MS-SEGURIDAD-BOMBEROS entre tests y runtime.
                    image 'python:3.11-slim'
                    reuseNode true
                }
            }
            steps {
                sh '''
                    rm -rf venv
                    python -m venv venv
                    . venv/bin/activate
                    pip install --no-cache-dir -r requirements-dev.txt
                    pytest tests/ \
                        --cov=modulo_documentos \
                        --cov-report=xml:coverage.xml \
                        --cov-report=term
                '''
            }
        }

        stage('SonarQube Analysis') {
            when {
                anyOf {
                    branch 'qa'
                    branch 'uat'
                    branch 'main'
                }
            }
            agent {
                dockerfile {
                    filename 'Dockerfile.ci'
                    reuseNode true
                }
            }
            environment {
                scannerHome = tool 'SonarScanner'
            }
            steps {
                script {
                    def sonarUserHome = "${env.WORKSPACE}/.sonar"
                    withEnv(["SONAR_USER_HOME=${sonarUserHome}"]) {
                        def projectKey = "BE-BOMBEROS-BANDEJA-${env.BRANCH_NAME.toUpperCase()}"
                        withSonarQubeEnv('SonarQube-Server') {
                            sh "${scannerHome}/bin/sonar-scanner -Dsonar.projectKey=${projectKey} -Dsonar.projectName='MS-BANDEJA-BOMBEROS-(${env.BRANCH_NAME})' -Dsonar.userHome=${sonarUserHome}"
                        }
                    }
                }
            }
        }

        stage('Quality Gate') {
            when {
                anyOf {
                    branch 'qa'
                    branch 'uat'
                    branch 'main'
                }
            }
            steps {
                timeout(time: 1, unit: 'HOURS') {
                    waitForQualityGate abortPipeline: true
                }
            }
        }

        // Trivy: se construye la imagen del backend y se escanea ANTES de
        // cualquier migración o deploy. Solo se tolera severidad LOW: si hay
        // MEDIUM, HIGH o CRITICAL el stage falla y el pipeline se detiene.
        // Sin excepciones: no hay --ignore-unfixed ni .trivyignore.
        stage('Build y Trivy (imagen Docker)') {
            environment {
                TRIVY_IMAGE = 'aquasec/trivy:0.58.1'
                IMAGEN = "bandeja-bomberos:${env.BUILD_NUMBER}"
            }
            steps {
                sh '''
                    set -e
                    docker build -t "$IMAGEN" .

                    TRIVY="docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache/ ${TRIVY_IMAGE}"

                    # 1) Reporte HTML (siempre se genera, para los artifacts y la presentación)
                    $TRIVY image --scanners vuln --severity LOW,MEDIUM,HIGH,CRITICAL \
                        --format template --template "@/contrib/html.tpl" \
                        "$IMAGEN" > trivy-report.html

                    # 2) Compuerta: MEDIUM, HIGH o CRITICAL detienen el pipeline
                    $TRIVY image --scanners vuln --severity MEDIUM,HIGH,CRITICAL \
                        --exit-code 1 --no-progress \
                        "$IMAGEN"
                '''
            }
            post {
                always {
                    archiveArtifacts artifacts: 'trivy-report.html', allowEmptyArchive: true
                    sh "docker rmi -f bandeja-bomberos:${env.BUILD_NUMBER} || true"
                }
            }
        }

        stage('Aplicar Migraciones (RDS)') {
            when {
                allOf {
                    expression { params.DB_HOST?.trim() != '' }
                    anyOf { branch 'development'; branch 'qa'; branch 'uat'; branch 'main' }
                }
            }
            agent {
                docker {
                    image 'python:3.11-slim'
                    reuseNode true
                }
            }
            steps {
                withEnv([
                    "DB_HOST=${params.DB_HOST}",
                    "DB_PORT=5432",
                    "DB_NAME=${params.DB_NAME}",
                    "DB_USER=${params.DB_USER}",
                    "DB_PASSWORD=${params.DB_PASSWORD}",
                ]) {
                    sh '''
                        pip install --no-cache-dir --quiet psycopg2-binary
                        python scripts/aplicar_migraciones.py
                    '''
                }
            }
        }

        stage('Integration Tests') {
            when { branch 'qa' }
            steps {
                script {
                    docker.image('postgres:16').withRun('-e POSTGRES_PASSWORD=test -e POSTGRES_DB=bandeja_test') { db ->
                        docker.image('python:3.11-slim').inside("--link ${db.id}:db") {
                            sh '''
                            python -m venv venv
                            . venv/bin/activate
                            pip install --no-cache-dir -r requirements-dev.txt
                            for i in $(seq 1 30); do
                                python -c "import psycopg2; psycopg2.connect(host='db', dbname='bandeja_test', user='postgres', password='test').close()" 2>/dev/null && break
                                sleep 1
                            done
                            TEST_DB_HOST=db TEST_DB_NAME=bandeja_test \
                            TEST_DB_USER=postgres TEST_DB_PASSWORD=test \
                                pytest -m integration -v
                            '''
                        }
                    }
                }
            }
        }

        stage('E2E Tests') {
            when { branch 'qa' }
            steps {
                withCredentials([file(credentialsId: 'BOMBEROS_BACKEND_E2E_QA', variable: 'E2E_ENV_FILE')]) {
                    sh 'tr -d "\\r" < "$E2E_ENV_FILE" > e2e/.env'
                }
                script {
                    def sha = sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
                    docker.image('node:20').inside {
                        withEnv([
                            "NPM_CONFIG_PREFIX=${env.WORKSPACE}/.npm-global",
                            "BRU=${env.WORKSPACE}/.npm-global/bin/bru",
                            "E2E_ESPERAR_COMMIT=${sha}"
                        ]) {
                            sh '''
                                set -a
                                . e2e/.env
                                set +a
                                npm install -g @usebruno/cli@4.2.1
                                bash scripts/correr_e2e.sh qa
                            '''
                        }
                    }
                }
            }
            post {
                always {
                    sh 'rm -f e2e/.env'
                    junit allowEmptyResults: true, testResults: 'e2e/reportes/junit.xml'
                    archiveArtifacts artifacts: 'e2e/reportes/**', allowEmptyArchive: true
                }
            }
        }

        stage('OWASP ZAP - Backend') {
            when { branch 'qa' }
            steps {
                script{
                    // Fase 1: preparar (necesita node + curl). Corre en node:20,-
                    // igual que las E2E, y deja el token y el OpenAPI en zap-work/.
                    docker.image('node:20').inside {
                        withCredentials([file(credentialsId: 'BOMBEROS_BACKEND_E2E_QA', variable: 'E2E_ENV_FILE')]) {
                            sh 'tr -d "\\r" < "$E2E_ENV_FILE" > e2e/.env'
                        }
                        sh '''
                            set -a
                            . e2e/.env
                            set +a
                            rm -f e2e/.env
                            bash scripts/correr_zap_backend.sh preparar qa
                        '''
                    }
                    // Fase 2: escanear (necesita docker). Corre en el agente, que
                    // ya tiene Docker (lo usan las E2E y las Integration Tests).
                    def status = sh(
                        script: 'bash scripts/correr_zap_backend.sh escanear',
                        returnStatus: true
                    )
                    if (status == 1) {
                        error('ZAP Backend: reglas de seguridad incumplidas (FAIL)')
                    } else if (status == 2) {
                        unstable('ZAP Backend: advertencias detectadas (WARN)')
                    } else if (status != 0) {
                        error("ZAP Backend: error de ejecucion (${status})")
                    }
                }
            }
            post {
                always {
                    archiveArtifacts artifacts: 'zap-reports/**', allowEmptyArchive: true
                }
            }
        }

    }
    post {
        always {
            deleteDir();
        }
    }
}
