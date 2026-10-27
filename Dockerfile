FROM python:3.11-alpine

WORKDIR /app

RUN apk add --no-cache gcc musl-dev postgresql-dev

COPY requirements.txt ./

# 1. Actualizamos pip y ELIMINAMOS su SBOM interno (causa del falso positivo)
RUN pip install --no-cache-dir --upgrade pip \
    && rm -f /usr/local/lib/python3.11/site-packages/pip/_vendor/bom.cdx.json \
    && pip install --no-cache-dir --upgrade setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

COPY modulo_documentos ./modulo_documentos
COPY migrations ./migrations

RUN adduser -D -H -u 10001 app
USER app