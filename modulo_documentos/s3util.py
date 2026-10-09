"""
Utilidades de S3 para el bucket de documentos (archivos PDF).

RNF-0006: máximo 20MB por archivo.
RNF-0007: validar el contenido real (firma %PDF-), no solo la extensión.
"""

import os
import uuid

import boto3

MAX_BYTES = 20 * 1024 * 1024  # 20 MB
PDF_MAGIC = b"%PDF-"

s3 = boto3.client("s3")


def _bucket() -> str:
    return os.environ["DOCUMENTS_BUCKET"]

def _owner() -> str:
    return os.environ["DOCUMENTS_BUCKET_OWNER"]

def generar_key() -> str:
    extension = ".pdf"
    return f"documentos/{uuid.uuid4()}{extension}"


def generar_url_subida(expira_segundos: int = 300) -> dict:
    """URL pre-firmada para que el cliente suba el PDF directo a S3 (evita el
    límite de payload de API Gateway/Lambda para archivos de hasta 20MB)."""
    key = generar_key()
    url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": _bucket(),
            "Key": key,
            "ContentType": "application/pdf",
        },
        ExpiresIn=expira_segundos,
    )
    return {"uploadUrl": url, "archivo_s3_key": key, "expiraEn": expira_segundos}


def generar_url_descarga(key: str, expira_segundos: int = 300) -> str:
    """RN-0023: descargar el documento en su formato original."""
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=expira_segundos,
    )


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

    if head["ContentLength"] > MAX_BYTES:
        return False

    inicio = s3.get_object(
        Bucket=_bucket(),
        Key=key,
        Range="bytes=0-4",
        ExpectedBucketOwner=_owner(),
    )
    firma = inicio["Body"].read()
    return firma.startswith(PDF_MAGIC)
