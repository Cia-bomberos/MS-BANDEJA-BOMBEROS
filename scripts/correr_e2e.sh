#!/usr/bin/env bash
# Corre la coleccion Bruno (e2e/) contra el API desplegado del stage indicado.
# Las credenciales (E2E_*_USUARIO / E2E_*_PASSWORD) las lee la coleccion
# del entorno del proceso con bru.getProcessEnv().
set -euo pipefail

STAGE="${1:?Uso: bash scripts/correr_e2e.sh <qa>}"
BRU="${BRU:-bru}"
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_BANDEJA="https://bomberos-f3-bandeja-cfg-${STAGE}.s3.amazonaws.com/bandeja-config.json"
CONFIG_SEGURIDAD="https://bomberos-config-${STAGE}.s3.amazonaws.com/config.json"

# Lee un campo de un JSON publicado en S3 y le quita las barras finales.
leer_json() {
  curl -fsS --retry 5 --retry-delay 3 "$1" | node -e '
    let s = "";
    process.stdin.on("data", d => s += d).on("end", () => {
      process.stdout.write(String(JSON.parse(s)[process.argv[1]]).replace(/\/+$/, ""));
    });' "$2"
}

# Jenkins corre en paralelo a GitHub Actions: se espera a que el deploy de ESTE
# commit este publicado para no probar la version anterior del API.
case "${E2E_ESPERAR_COMMIT:-}" in null|undefined) E2E_ESPERAR_COMMIT="" ;; esac
if [ -n "${E2E_ESPERAR_COMMIT:-}" ]; then
  ACTUAL=""
  for _ in $(seq 1 60); do
    ACTUAL=$(leer_json "$CONFIG_BANDEJA" commit 2>/dev/null || true)
    [ "$ACTUAL" = "$E2E_ESPERAR_COMMIT" ] && break
    echo "Esperando el deploy del commit ${E2E_ESPERAR_COMMIT:0:7} (el API esta en: ${ACTUAL:0:7})..."
    sleep 15
  done
  if [ "$ACTUAL" != "$E2E_ESPERAR_COMMIT" ]; then
    echo "ERROR: el deploy de GitHub Actions para el commit ${E2E_ESPERAR_COMMIT:0:7} no se publico en 15 minutos." >&2
    exit 1
  fi
fi

# API de la bandeja: la misma config que lee el frontend.
E2E_BASE_URL="${E2E_BASE_URL:-$(leer_json "$CONFIG_BANDEJA" apiUrl)}"
# App client de Cognito: sale del config.json publico de seguridad.
E2E_COGNITO_CLIENT_ID="${E2E_COGNITO_CLIENT_ID:-$(leer_json "$CONFIG_SEGURIDAD" clientId)}"
echo "API bajo prueba: ${E2E_BASE_URL}"

mkdir -p "$RAIZ/e2e/reportes"
cd "$RAIZ/e2e"

"$BRU" run -r --env "$STAGE" \
  --env-var "baseUrl=${E2E_BASE_URL}" \
  --env-var "cognitoClientId=${E2E_COGNITO_CLIENT_ID}" \
  --reporter-skip-headers Authorization \
  --reporter-junit reportes/junit.xml \
  --reporter-html reportes/reporte.html