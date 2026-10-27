# Imagen del backend (mismas dependencias y código que se empaquetan en las
# Lambdas, runtime python3.11). El pipeline de Jenkins la construye solo para
# escanearla con Trivy antes de desplegar; el deploy real sigue siendo
# Serverless Framework.
FROM python:3.11-alpine

WORKDIR /app

RUN apk add --no-cache gcc musl-dev postgresql-dev

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

COPY modulo_documentos ./modulo_documentos
COPY migrations ./migrations

# Sin privilegios de root dentro de la imagen
RUN adduser -D -H -u 10001 app
USER app
