#!/usr/bin/env bash
# OWASP ZAP API Scan contra el API desplegado de la bandeja (stage indicado).
#
# Dos fases, porque necesitan herramientas distintas:
#   preparar <stage>  curl + node (en Jenkins: dentro de node:20). Obtiene la URL del
#                     API, hace login como Jefatura y deja todo listo en zap-work/.
#   escanear          docker (en el agente de Jenkins). Corre ZAP y deja los reportes
#                     en zap-reports/. Sale con el codigo de ZAP:
#                     0 = limpio, 1 = hay FAIL, 2 = hay WARN, 3 = error tecnico.
set -euo pipefail

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RAIZ"
FASE="${1:?Uso: bash scripts/correr_zap_backend.sh preparar <stage> | escanear}"

# Lee un campo de un JSON publicado en S3 y le quita las barras finales.
leer_json() {
  curl -fsS --retry 5 --retry-delay 3 "$1" | node -e '
    let s = "";
    process.stdin.on("data", d => s += d).on("end", () => {
      process.stdout.write(String(JSON.parse(s)[process.argv[1]]).replace(/\/+$/, ""));
    });' "$2"
}

preparar() {
  STAGE="${1:?Uso: bash scripts/correr_zap_backend.sh preparar <stage>}"
  : "${E2E_JEFATURA_USUARIO:?falta E2E_JEFATURA_USUARIO}"
  : "${E2E_JEFATURA_PASSWORD:?falta E2E_JEFATURA_PASSWORD}"

  API_URL=$(leer_json "https://bomberos-f3-bandeja-cfg-${STAGE}.s3.amazonaws.com/bandeja-config.json" apiUrl)
  CLIENT_ID=$(leer_json "https://bomberos-config-${STAGE}.s3.amazonaws.com/config.json" clientId)
  echo "API bajo prueba: ${API_URL}"

  rm -rf zap-work
  mkdir -p zap-work

  chmod -R 0777 zap-work

  # Login en Cognito como Jefatura (ve y gestiona todas las secciones).
  CUERPO=$(CLIENT_ID="$CLIENT_ID" node -e '
    process.stdout.write(JSON.stringify({
      AuthFlow: "USER_PASSWORD_AUTH",
      ClientId: process.env.CLIENT_ID,
      AuthParameters: {
        USERNAME: process.env.E2E_JEFATURA_USUARIO,
        PASSWORD: process.env.E2E_JEFATURA_PASSWORD
      }
    }));')
  RESPUESTA=$(curl -sS -X POST "https://cognito-idp.us-east-1.amazonaws.com/" \
    -H "Content-Type: application/x-amz-json-1.1" \
    -H "X-Amz-Target: AWSCognitoIdentityProviderService.InitiateAuth" \
    -d "$CUERPO")
  printf '%s' "$RESPUESTA" | node -e '
    let s = "";
    process.stdin.on("data", d => s += d).on("end", () => {
      const r = JSON.parse(s);
      if (r.ChallengeName) {
        console.error("Cognito pide el reto " + r.ChallengeName + " (cambiar la contrasena temporal)");
        process.exit(1);
      }
      if (!r.AuthenticationResult) {
        console.error("Login fallido en Cognito: " + (r.__type || "sin detalle"));
        process.exit(1);
      }
      process.stdout.write(r.AuthenticationResult.IdToken);
    });' > zap-work/token

  # Comprobacion previa: si el token no sirve, el escaneo no tiene sentido.
  CODIGO=$(curl -sS -o zap-work/prueba.json -w '%{http_code}' \
    -H "Authorization: $(cat zap-work/token)" "${API_URL}/documentos")
  rm -f zap-work/prueba.json
  if [ "$CODIGO" != "200" ]; then
    echo "ERROR: el API no acepta el token de Jefatura (HTTP ${CODIGO})." >&2
    exit 1
  fi
  echo "Login de Jefatura correcto; el API responde 200."

  # OpenAPI con la URL real del stage.
  sed "s|__API_URL__|${API_URL}|g" zap/openapi.yaml > zap-work/openapi.yaml

  # ZAP exige tabuladores entre columnas; en el repo se edita con espacios.
  awk '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { print; next }
    { id = $1; accion = $2; $1 = ""; $2 = ""; sub(/^[[:space:]]+/, "")
      printf "%s\t%s\t%s\n", id, accion, $0 }
  ' zap/backend-rules.conf > zap-work/backend-rules.conf
  echo "Preparacion lista en zap-work/"
}

escanear() {
  export MSYS_NO_PATHCONV=1
  [ -s zap-work/token ] || { echo "Falta zap-work/token: corre antes la fase 'preparar'." >&2; exit 64; }
  trap 'rm -rf zap-work; docker rm -f "$NOMBRE" >/dev/null 2>&1 || true' EXIT

  TOKEN=$(tr -d '\r\n' < zap-work/token)
  NOMBRE="zap-backend-$$"
  mkdir -p zap-reports

  # Arranca el contenedor en reposo (sin volumen montado)
  docker run -d --name "$NOMBRE" --entrypoint sleep ghcr.io/zaproxy/zaproxy:stable 3600 > /dev/null

  # Crea /zap/wrk DENTRO del contenedor y copia los archivos ahí
  docker exec -u root "$NOMBRE" mkdir -p /zap/wrk
  docker cp zap-work/openapi.yaml "$NOMBRE":/zap/wrk/openapi.yaml
  docker cp zap-work/backend-rules.conf "$NOMBRE":/zap/wrk/backend-rules.conf
  docker cp zap-work/token "$NOMBRE":/zap/wrk/token

  # CRÍTICO: los archivos copiados pertenecen a root. ZAP corre como uid 1000.
  # Damos permisos de lectura/escritura a todos para evitar "Permission denied".
  docker exec -u root "$NOMBRE" chmod -R 0777 /zap/wrk

  # Verificación en el log: debe listar los 3 archivos
  docker exec "$NOMBRE" ls -la /zap/wrk

  # Ejecuta ZAP dentro del contenedor. El token se pasa como variable de entorno.
  ESTADO=0
  docker exec -e ZAP_AUTH_HEADER_VALUE="${TOKEN}" "$NOMBRE" \
    zap-api-scan.py \
      -t openapi.yaml -f openapi -c backend-rules.conf -S \
      -r backend-zap.html -J backend-zap.json || ESTADO=$?

  # Extrae los reportes del contenedor
  docker cp "$NOMBRE":/zap/wrk/backend-zap.html zap-reports/ 2>/dev/null || true
  docker cp "$NOMBRE":/zap/wrk/backend-zap.json zap-reports/ 2>/dev/null || true

  echo "ZAP termino con codigo ${ESTADO} (0 limpio, 1 FAIL, 2 WARN, 3 error tecnico)."
  exit "$ESTADO"
}

case "$FASE" in
  preparar) preparar "${2:-}" ;;
  escanear) escanear ;;
  *) echo "Fase desconocida: ${FASE}" >&2; exit 64 ;;
esac