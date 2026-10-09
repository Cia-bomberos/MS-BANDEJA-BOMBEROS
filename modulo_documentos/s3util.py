"""
Utilidades de S3 para el bucket de documentos (archivos PDF).

RNF-0006: máximo 20MB por archivo.
RNF-0007: validar el contenido real (firma %PDF-), no solo la extensión.
"""

import logging
import os
import uuid
import boto3
import re

MAX_BYTES = 20 * 1024 * 1024  # 20 MB
PDF_MAGIC = b"%PDF-"
PDF_EOF = b"%%EOF"
PDF_MIN_BYTES = 100

_RE_OBJ_PDF = re.compile(rb"(?m)^\d+\s+\d+\s+obj\b")

logger = logging.getLogger(__name__)

# Las subidas llegan primero a "pendientes/" (con expiración automática de 1 día,
# ver serverless.yml). Solo al registrar/reemplazar con un PDF válido el archivo
# se mueve a "documentos/". Así, lo que se sube y nunca se registra (o se rechaza)
# no se acumula en el bucket.
PREFIJO_PENDIENTES = "pendientes/"
PREFIJO_DOCUMENTOS = "documentos/"

s3 = boto3.client("s3")


def _bucket() -> str:
    return os.environ["DOCUMENTS_BUCKET"]


def _owner() -> str:
    """Cuenta dueña del bucket: se envía como ExpectedBucketOwner para que S3 rechace
    la operación si el nombre del bucket llegara a apuntar a un bucket ajeno."""
    return os.environ["DOCUMENTS_BUCKET_OWNER"]


def generar_key() -> str:
    extension = ".pdf"
    return f"{PREFIJO_PENDIENTES}{uuid.uuid4()}{extension}"


def generar_url_subida(expira_segundos: int = 300, tamano_bytes: int | None = None) -> dict:
    """URL pre-firmada para que el cliente suba el PDF directo a S3 (evita el
    límite de payload de API Gateway/Lambda para archivos de hasta 20MB).

    Si el cliente informa `tamano_bytes`, el tamaño queda firmado en la URL: S3
    rechaza una subida con otro Content-Length, así que no se puede declarar
    1 KB y subir 2 GB."""
    key = generar_key()
    params = {
        "Bucket": _bucket(),
        "Key": key,
        "ContentType": "application/pdf",
    }
    if tamano_bytes is not None:
        params["ContentLength"] = tamano_bytes
    url = s3.generate_presigned_url("put_object", Params=params, ExpiresIn=expira_segundos)
    return {"uploadUrl": url, "archivo_s3_key": key, "expiraEn": expira_segundos}


def generar_url_descarga(key: str, expira_segundos: int = 300) -> str:
    """RN-0023: descargar el documento en su formato original."""
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=expira_segundos,
    )

def _leer_rango(key: str, rango: str) -> bytes:
    try:
        resp = s3.get_object(
            Bucket=_bucket(),
            Key=key,
            Range=rango,
            ExpectedBucketOwner=_owner(),
        )
    except s3.exceptions.ClientError:
        return b""
    return resp["Body"].read()

def validar_pdf(key: str) -> bool:
    """Verifica tamaño (<=20MB) y que el contenido real sea un PDF (firma %PDF-)."""
    try:
        head = s3.head_object(
            Bucket=_bucket(),
            Key=key,
            ExpectedBucketOwner=_owner(),
        )
    except s3.exceptions.ClientError:
        return False
    tamanio = head["ContentLength"]
    if tamanio > MAX_BYTES or tamanio < PDF_MIN_BYTES:
        return False

    if not _leer_rango(key, "bytes=0-4").startswith(PDF_MAGIC):
        return False
    cola = _leer_rango(key, f"bytes=-{min(1024, tamanio)}")
    if PDF_EOF not in cola:
        return False
    cuerpo = _leer_rango(key, "bytes=0-2047")
    if not _RE_OBJ_PDF.search(cuerpo):
        return False
    
    return True


def eliminar(key: str) -> None:
    """Borra un objeto del bucket de documentos. Nunca lanza: una limpieza
    fallida no debe tumbar la operación del usuario (la regla de expiración
    del bucket cubre lo que quede en 'pendientes/')."""
    try:
        s3.delete_object(Bucket=_bucket(), Key=key, ExpectedBucketOwner=_owner())
    except Exception as e:  # noqa: BLE001
        logger.warning("No se pudo eliminar %s de S3: %s", key, e)


def promover_pdf(key: str) -> str | None:
    """Confirma una subida: valida que sea un PDF de hasta 20MB y lo mueve de
    'pendientes/' a 'documentos/'. Devuelve la key definitiva, o None si el
    archivo no es válido (en ese caso se elimina de S3: no queda basura).

    Solo acepta keys de 'pendientes/': una key ajena (otro documento, la
    carpeta de configuración, etc.) se rechaza y NUNCA se borra."""
    if not isinstance(key, str) or not key.startswith(PREFIJO_PENDIENTES):
        return None
    if not validar_pdf(key):
        eliminar(key)
        return None
    definitiva = generar_key_definitiva()
    s3.copy_object(
        Bucket=_bucket(),
        Key=definitiva,
        CopySource={"Bucket": _bucket(), "Key": key},
        ExpectedBucketOwner=_owner(),
        ExpectedSourceBucketOwner=_owner(),
    )
    eliminar(key)
    return definitiva


def generar_key_definitiva() -> str:
    return f"{PREFIJO_DOCUMENTOS}{uuid.uuid4()}.pdf"
