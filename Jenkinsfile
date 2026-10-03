pipeline {
    agent any

    options {
        buildDiscarder(logRotator(numToKeepStr: '5'))
        disableConcurrentBuilds()
        timestamps()
        skipDefaultCheckout(true)
    }

    // Credenciales dinámicas de AWS Learner Lab (rotan periódicamente) y de
    // la BD propia del equipo (mismo .env que se le pasa a Nico). Todas
    // opcionales: sin credenciales de AWS el deploy se omite y solo corren
    // tests + SonarQube (igual que en MS-SEGURIDAD-BOMBEROS).
    parameters {
        string(name: 'AWS_ACCESS_KEY_ID', defaultValue: '', description: 'Access Key de AWS Learner Lab')
        password(name: 'AWS_SECRET_ACCESS_KEY', defaultValue: '', description: 'Secret Access Key de AWS Learner Lab')
        password(name: 'AWS_SESSION_TOKEN', defaultValue: '', description: 'Session Token de AWS Learner Lab')
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

        stage('Deploy omitido (sin credenciales)') {
            when {
                allOf {
                    expression { params.AWS_ACCESS_KEY_ID?.trim() == '' }
                    anyOf { branch 'development'; branch 'qa'; branch 'uat'; branch 'main' }
                }
            }
            steps {
                echo 'No se pasaron credenciales de AWS Learner Lab (Build with Parameters). Se omite el deploy; solo corrieron tests y SonarQube.'
            }
        }

        stage('Deploy Dev') {
            when {
                allOf { branch 'development'; expression { params.AWS_ACCESS_KEY_ID?.trim() != '' } }
            }
            steps { executeServerlessDeploy('dev') }
        }

        stage('Deploy QA') {
            when {
                allOf { branch 'qa'; expression { params.AWS_ACCESS_KEY_ID?.trim() != '' } }
            }
            steps { executeServerlessDeploy('qa') }
        }

        stage('Deploy UAT') {
            when {
                allOf { branch 'uat'; expression { params.AWS_ACCESS_KEY_ID?.trim() != '' } }
            }
            steps { executeServerlessDeploy('uat') }
        }

        stage('Deploy Prod') {
            when {
                allOf { branch 'main'; expression { params.AWS_ACCESS_KEY_ID?.trim() != '' } }
            }
            steps { executeServerlessDeploy('prod') }
        }
    }
}

// Despliega con Serverless Framework y publica la config (apiUrl del stage)
// en el mismo bucket de config que ya usa MS-SEGURIDAD-BOMBEROS
// (bomberos-config-<stage>), bajo una key separada (bandeja-config.json)
// para no pisar el config.json de seguridad. Así el frontend sigue
// leyendo datos actualizados sin importar cuándo rotaron las credenciales
// de AWS Academy -- mismo mecanismo que ya resolvió el PM para seguridad.
def executeServerlessDeploy(String targetStage) {
    docker.image('node:20').inside {
        withEnv([
            "AWS_ACCESS_KEY_ID=${params.AWS_ACCESS_KEY_ID}",
            "AWS_SECRET_ACCESS_KEY=${params.AWS_SECRET_ACCESS_KEY}",
            "AWS_SESSION_TOKEN=${params.AWS_SESSION_TOKEN}",
            "AWS_DEFAULT_REGION=us-east-1",
            "DB_HOST=${params.DB_HOST}",
            "DB_PORT=5432",
            "DB_NAME=${params.DB_NAME}",
            "DB_USER=${params.DB_USER}",
            "DB_PASSWORD=${params.DB_PASSWORD}",
            "NPM_CONFIG_PREFIX=${env.WORKSPACE}/.npm-global"
        ]) {
            sh """
                apt-get update -qq && apt-get install -y -qq python3 python3-pip > /dev/null
                pip install --break-system-packages --quiet boto3
            """
            // Falla rápido y con un mensaje claro si falta MS-SEGURIDAD-BOMBEROS
            // en este stage, o crea el bucket de config si todavía no existe.
            sh "python3 scripts/verificar_dependencias.py --stage ${targetStage}"
            sh """
                npm install -g serverless@3
                npm install
                ./.npm-global/bin/serverless deploy --stage ${targetStage} --verbose
            """
            sh "python3 scripts/publicar_config.py --stage ${targetStage}"
        }
    }
}
