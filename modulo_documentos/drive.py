"""
Integración con Google Drive (RN-0027 / RF-0009): respalda en la carpeta de la
Compañía los documentos al archivarse.

Configuración (nada de esto va en el repo):
- DRIVE_FOLDER_ID: ID de la carpeta de Drive destino (variable de entorno). La
  carpeta debe ser de la cuenta de Google que autorizó el acceso (ver abajo).
- Credenciales OAuth de esa cuenta, guardadas en el bucket privado de documentos
  en DRIVE_CREDENTIALS_S3_KEY (por defecto config/google-oauth.json), con el formato
  {"client_id": "...", "client_secret": "...", "refresh_token": "..."}. Se generan
  una sola vez con scripts/obtener_token_drive.py.

Por qué OAuth de usuario y no cuenta de servicio: una cuenta de servicio no tiene
almacenamiento propio, así que en un Gmail personal (Mi unidad) falla con
`storageQuotaExceeded`. Con OAuth los archivos quedan a nombre de la persona que
autorizó y cuentan para su espacio. Las credenciales van en S3 y no en variables de
entorno porque Lambda limita todas las variables a 4KB.

Si DRIVE_FOLDER_ID no está definido, `configurado()` es False y el job de
archivado no confirma nada (el documento queda pendiente de respaldo).
"""

import json
import logging
import os
import uuid

import boto3
import requests

logger = logging.getLogger()

TIMEOUT_SEGUNDOS = 20  # RNF-0009: nunca colgar el job por Google
API_FILES = "https://www.googleapis.com/drive/v3/files"
API_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CREDENCIALES_KEY_DEFECTO = "config/google-oauth.json"


def configurado() -> bool:
    return bool(os.environ.get("DRIVE_FOLDER_ID", "").strip())


def _carpeta() -> str:
    return os.environ["DRIVE_FOLDER_ID"].strip()


def _token(s3=None) -> str:
    """Cambia el refresh_token guardado en S3 por un access_token de corta duración."""
    s3 = s3 or boto3.client("s3")
    key = os.environ.get("DRIVE_CREDENTIALS_S3_KEY", CREDENCIALES_KEY_DEFECTO)
    cred = json.loads(s3.get_object(Bucket=os.environ["DOCUMENTS_BUCKET"], Key=key)["Body"].read())
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": cred["client_id"],
            "client_secret": cred["client_secret"],
            "refresh_token": cred["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=TIMEOUT_SEGUNDOS,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def nombre_archivo(codigo_unico, documento_id) -> str:
    """Nombre legible y válido en Drive: el código único (si existe) o el id."""
    base = str(codigo_unico) if codigo_unico else str(documento_id)
    for ch in '/\\:*?"<>|':
        base = base.replace(ch, "-")
    return f"{base}.pdf"


def buscar_existente(nombre: str, token: str):
    """Evita duplicados si un reintento llega después de una subida que sí funcionó."""
    nombre_q = nombre.replace("\\", "\\\\").replace("'", "\\'")
    q = f"name = '{nombre_q}' and '{_carpeta()}' in parents and trashed = false"
    resp = requests.get(
        API_FILES,
        params={"q": q, "fields": "files(id)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT_SEGUNDOS,
    )
    resp.raise_for_status()
    archivos = resp.json().get("files", [])
    return archivos[0]["id"] if archivos else None


def subir_pdf(nombre: str, contenido: bytes, token: str) -> str:
    """Sube el PDF a la carpeta configurada y devuelve el id del archivo en Drive."""
    existente = buscar_existente(nombre, token)
    if existente:
        logger.info("Drive: '%s' ya existía (%s); no se vuelve a subir.", nombre, existente)
        return existente

    limite = uuid.uuid4().hex
    metadata = json.dumps({"name": nombre, "parents": [_carpeta()], "mimeType": "application/pdf"})
    cuerpo = (
        f"--{limite}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{metadata}\r\n"
        f"--{limite}\r\nContent-Type: application/pdf\r\n\r\n"
    ).encode("utf-8") + contenido + f"\r\n--{limite}--".encode("utf-8")

    resp = requests.post(
        API_UPLOAD,
        params={"uploadType": "multipart", "supportsAllDrives": "true", "fields": "id"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/related; boundary={limite}"},
        data=cuerpo,
        timeout=TIMEOUT_SEGUNDOS,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def respaldar(documento_id, codigo_unico, archivo_s3_key, s3=None) -> bool:
    """Copia el PDF de S3 a Drive. True SOLO si Drive confirmó el archivo.
    Cualquier fallo (red, permisos, credenciales) devuelve False sin lanzar."""
    if not configurado():
        logger.warning("Drive no configurado (falta DRIVE_FOLDER_ID): documento %s sin respaldar.", documento_id)
        return False
    try:
        s3 = s3 or boto3.client("s3")
        contenido = s3.get_object(Bucket=os.environ["DOCUMENTS_BUCKET"], Key=archivo_s3_key)["Body"].read()
        token = _token(s3)
        file_id = subir_pdf(nombre_archivo(codigo_unico, documento_id), contenido, token)
        logger.info("Drive: documento %s respaldado (%s).", documento_id, file_id)
        return True
    except Exception as e:  # noqa: BLE001 -- el job debe seguir con el resto
        logger.error("Drive: falló el respaldo del documento %s: %s", documento_id, e)
        return False
