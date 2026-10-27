FROM python:3.11-alpine

WORKDIR /app

# 1. Instalamos dependencias del sistema
RUN apk add --no-cache gcc musl-dev postgresql-dev

COPY requirements.txt ./

# 2. ESTRATEGIA DE SEGURIDAD:
# - Forzamos setuptools a la versión 84.2.1 (que Trivy marca como segura y ya existe).
# - Forzamos urllib3 a 2.8.0 (el parche de seguridad).
# - Forzamos msgpack a 1.2.1 (el parche de seguridad).
# - Usamos --no-cache-dir para no dejar rastro.
RUN pip install --no-cache-dir --upgrade pip wheel \
    && pip install --no-cache-dir --force-reinstall "setuptools>=84.2.1" \
    && pip install --no-cache-dir "urllib3>=2.8.0" "msgpack>=1.2.1" \
    && pip install --no-cache-dir -r requirements.txt \
    && find /usr/local/lib/python3.11 -name "*.dist-info" -type d -exec rm -rf {} + || true \
    && rm -rf /root/.cache /tmp/*

COPY modulo_documentos ./modulo_documentos
COPY migrations ./migrations

RUN adduser -D -H -u 10001 app
USER app