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

Carpeta raíz: la fija el Jefe de Administración desde el front (tabla `configuracion`,
clave `drive`); DRIVE_FOLDER_ID es solo el valor de respaldo por defecto. Dentro de la raíz
cada PDF va a `<Año>/<MM - Mes>/` según la fecha de REGISTRO del documento (hora de Lima),
así un reintento siempre cae en la misma carpeta.

Si no hay carpeta (ni configurada ni en DRIVE_FOLDER_ID), `configurado()` es False y el job
de archivado no confirma nada (el documento queda pendiente de respaldo).
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime

import boto3
import requests

from modulo_documentos.tiempo import LIMA, ahora_lima

logger = logging.getLogger()

TIMEOUT_SEGUNDOS = 20  # RNF-0009: nunca colgar el job por Google
API_FILES = "https://www.googleapis.com/drive/v3/files"
API_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_ABOUT = "https://www.googleapis.com/drive/v3/about"
CREDENCIALES_KEY_DEFECTO = "config/google-oauth.json"
MIME_CARPETA = "application/vnd.google-apps.folder"
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
         "Septiembre", "Octubre", "Noviembre", "Diciembre"]


class ErrorCarpeta(Exception):
    """La carpeta indicada no sirve para respaldar (no existe, no es carpeta o no se puede escribir)."""


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


def _q(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("'", "\\'")


def buscar_existente(nombre: str, token: str, carpeta: str | None = None):
    """Evita duplicados si un reintento llega después de una subida que sí funcionó."""
    q = f"name = '{_q(nombre)}' and '{carpeta or _carpeta()}' in parents and trashed = false"
    resp = requests.get(
        API_FILES,
        params={"q": q, "fields": "files(id)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT_SEGUNDOS,
    )
    resp.raise_for_status()
    archivos = resp.json().get("files", [])
    return archivos[0]["id"] if archivos else None


def subir_pdf(nombre: str, contenido: bytes, token: str, carpeta: str | None = None) -> str:
    """Sube el PDF a `carpeta` (por defecto la de DRIVE_FOLDER_ID) y devuelve el id del archivo."""
    carpeta = carpeta or _carpeta()
    existente = buscar_existente(nombre, token, carpeta)
    if existente:
        logger.info("Drive: '%s' ya existía (%s); no se vuelve a subir.", nombre, existente)
        return existente

    limite = uuid.uuid4().hex
    metadata = json.dumps({"name": nombre, "parents": [carpeta], "mimeType": "application/pdf"})
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


def nombres_ruta(fecha) -> tuple[str, str]:
    """('2026', '10 - Octubre') a partir de la fecha de registro, en hora de Lima."""
    if fecha is None:
        fecha = ahora_lima()
    elif isinstance(fecha, datetime):
        fecha = fecha.astimezone(LIMA) if fecha.tzinfo else fecha.replace(tzinfo=LIMA)
    return str(fecha.year), f"{fecha.month:02d} - {MESES[fecha.month - 1]}"


def _buscar_carpeta(nombre: str, padre: str, token: str):
    q = (f"name = '{_q(nombre)}' and '{padre}' in parents and mimeType = '{MIME_CARPETA}' "
         "and trashed = false")
    resp = requests.get(
        API_FILES,
        params={"q": q, "fields": "files(id)", "orderBy": "createdTime",
                "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT_SEGUNDOS,
    )
    resp.raise_for_status()
    carpetas = resp.json().get("files", [])
    return carpetas[0]["id"] if carpetas else None  # si hubiera duplicadas, la más antigua


def asegurar_carpeta(nombre: str, padre: str, token: str) -> str:
    """Devuelve el id de la subcarpeta `nombre` dentro de `padre`; la crea si no existe."""
    existente = _buscar_carpeta(nombre, padre, token)
    if existente:
        return existente
    resp = requests.post(
        API_FILES,
        params={"supportsAllDrives": "true", "fields": "id"},
        headers={"Authorization": f"Bearer {token}"},
        json={"name": nombre, "mimeType": MIME_CARPETA, "parents": [padre]},
        timeout=TIMEOUT_SEGUNDOS,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def carpeta_destino(fecha, raiz: str, token: str) -> str:
    """Año/Mes bajo la carpeta raíz (se crean la primera vez que hacen falta)."""
    anio, mes = nombres_ruta(fecha)
    return asegurar_carpeta(mes, asegurar_carpeta(anio, raiz, token), token)


# --- Configuración del enlace (la edita el Jefe de Administración desde el front) ---

_ID_CARPETA = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_HOSTS_DRIVE = ("drive.google.com", "docs.google.com")


def extraer_folder_id(texto) -> str:
    """Acepta el enlace de una carpeta de Drive (…/folders/<ID>, …?id=<ID>) o el ID suelto.
    Lanza ValueError si no es reconocible."""
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("Debe indicar el enlace de la carpeta de Drive.")
    if _ID_CARPETA.match(texto):
        return texto
    m = re.match(r"^https://([^/?#]+)(/[^?#]*)?(\?[^#]*)?", texto)
    if not m or m.group(1).lower() not in _HOSTS_DRIVE:
        raise ValueError("El enlace debe ser de una carpeta de Google Drive (https://drive.google.com/drive/folders/…).")
    ruta, consulta = m.group(2) or "", m.group(3) or ""
    for patron, fuente in ((r"/folders/([A-Za-z0-9_-]+)", ruta), (r"[?&]id=([A-Za-z0-9_-]+)", consulta)):
        hallado = re.search(patron, fuente)
        if hallado and _ID_CARPETA.match(hallado.group(1)):
            return hallado.group(1)
    raise ValueError("No se encontró el ID de la carpeta en el enlace. Abra la carpeta en Drive y copie su enlace.")


def enlace_carpeta(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


def verificar_carpeta(folder_id: str, token: str) -> dict:
    """Comprueba que la cuenta de respaldo ve la carpeta y puede crear archivos en ella.
    Devuelve {"id", "nombre"}; lanza ErrorCarpeta con un motivo comprensible si no."""
    resp = requests.get(
        f"{API_FILES}/{folder_id}",
        params={"fields": "id,name,mimeType,capabilities/canAddChildren", "supportsAllDrives": "true"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT_SEGUNDOS,
    )
    if resp.status_code in (403, 404):
        raise ErrorCarpeta("La carpeta no existe o no está compartida con la cuenta de respaldo.")
    resp.raise_for_status()
    datos = resp.json()
    if datos.get("mimeType") != MIME_CARPETA:
        raise ErrorCarpeta("El enlace no corresponde a una carpeta (parece un archivo).")
    if not datos.get("capabilities", {}).get("canAddChildren"):
        raise ErrorCarpeta("La cuenta de respaldo solo puede ver la carpeta: debe compartirla como Editor.")
    return {"id": datos["id"], "nombre": datos.get("name")}


def cuenta_respaldo(token: str):
    """Correo de la cuenta de Google con la que se respalda (a quien hay que compartir la
    carpeta). None si no se puede averiguar."""
    try:
        resp = requests.get(API_ABOUT, params={"fields": "user(emailAddress)"},
                            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT_SEGUNDOS)
        resp.raise_for_status()
        return resp.json().get("user", {}).get("emailAddress")
    except Exception:  # noqa
        return None


def respaldar(documento_id, codigo_unico, archivo_s3_key, s3=None, fecha=None, raiz=None):
    """Copia el PDF de S3 a Drive, en `<raíz>/<Año>/<Mes>/` según `fecha` (la de registro).
    Devuelve {"file_id", "folder_id"} SOLO si Drive confirmó el archivo; None si no.
    Cualquier fallo (red, permisos, credenciales) devuelve None sin lanzar."""
    raiz = (raiz or os.environ.get("DRIVE_FOLDER_ID", "")).strip()
    if not raiz:
        logger.warning("Drive no configurado (sin carpeta): documento %s sin respaldar.", documento_id)
        return None
    try:
        s3 = s3 or boto3.client("s3")
        contenido = s3.get_object(Bucket=os.environ["DOCUMENTS_BUCKET"], Key=archivo_s3_key)["Body"].read()
        token = _token(s3)
        destino = carpeta_destino(fecha, raiz, token)
        file_id = subir_pdf(nombre_archivo(codigo_unico, documento_id), contenido, token, destino)
        logger.info("Drive: documento %s respaldado (%s) en la carpeta %s.", documento_id, file_id, destino)
        return {"file_id": file_id, "folder_id": destino}
    except Exception as e:  # noqa: BLE001 -- el job debe seguir con el resto
        logger.exception("Drive: falló el respaldo del documento %s: %s", documento_id, e)
        return None
