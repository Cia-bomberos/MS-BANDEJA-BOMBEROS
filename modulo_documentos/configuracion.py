"""
Configuración de la carpeta de Google Drive para el respaldo (RN-0027 / RF-0009).

  GET /configuracion/drive   estado actual
  PUT /configuracion/drive   registra o actualiza el enlace de la carpeta

Solo el Jefe de Administración puede verla o cambiarla. Lo que se guarda es el ID de la
carpeta (no es un secreto); las credenciales con las que se escribe en ella viven en S3
y nunca pasan por el front (ver drive.py). Para que el respaldo funcione, la carpeta debe
estar compartida como Editor con la cuenta de Google de respaldo: el GET devuelve su correo.

Cambiar la carpeta NO mueve lo ya archivado: cada documento conserva en `drive_folder_id`
dónde quedó. Los respaldos nuevos y los reintentos pendientes usan la carpeta vigente.
"""

import json
import logging
import os

from modulo_documentos import auth, drive
from modulo_documentos.db import get_connection
from modulo_documentos.handler import _respuesta

logger = logging.getLogger()


def _estado(fila, cuenta=None) -> dict:
    """Arma la respuesta a partir de la fila de `configuracion` (o de la variable de entorno)."""
    if fila:
        valor = fila["valor"]
        return {
            "configurado": True,
            "origen": "configuracion",
            "carpeta_id": valor["folder_id"],
            "carpeta_nombre": valor.get("nombre"),
            "enlace": drive.enlace_carpeta(valor["folder_id"]),
            "actualizado_por": fila.get("actualizado_nombre"),
            "actualizado_en": fila.get("actualizado_en"),
            "cuenta_respaldo": cuenta,
        }
    por_defecto = os.environ.get("DRIVE_FOLDER_ID", "").strip()
    return {
        "configurado": bool(por_defecto),
        "origen": "variable_de_entorno" if por_defecto else None,
        "carpeta_id": por_defecto or None,
        "carpeta_nombre": None,
        "enlace": drive.enlace_carpeta(por_defecto) if por_defecto else None,
        "actualizado_por": None,
        "actualizado_en": None,
        "cuenta_respaldo": cuenta,
    }


def _token_o_none():
    try:
        return drive._token()
    except Exception as e:  # noqa: BLE001
        logger.error("Drive: no se pudo obtener el token de acceso: %s", e)
        return None


def _solo_administracion(event):
    if not auth.es_jefe_administracion(event):
        return _respuesta(403, {"error": "Solo el Jefe de Administración puede configurar la carpeta de Drive."})
    return None


def obtener_config_drive(event, context):
    """GET /configuracion/drive."""
    denegado = _solo_administracion(event)
    if denegado:
        return denegado
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT valor, actualizado_nombre, actualizado_en FROM configuracion WHERE clave = 'drive'")
            fila = cur.fetchone()
        token = _token_o_none()
        return _respuesta(200, _estado(fila, drive.cuenta_respaldo(token) if token else None))
    finally:
        conn.close()


def guardar_config_drive(event, context):
    """PUT /configuracion/drive  {"enlace": "https://drive.google.com/drive/folders/<ID>"}.
    Comprueba contra Drive que la carpeta existe y que la cuenta de respaldo puede escribir
    en ella antes de guardarla."""
    denegado = _solo_administracion(event)
    if denegado:
        return denegado

    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        return _respuesta(400, {"error": "El cuerpo debe ser JSON."})
    try:
        folder_id = drive.extraer_folder_id(body.get("enlace") or body.get("link"))
    except ValueError as e:
        return _respuesta(400, {"error": str(e)})

    token = _token_o_none()
    if token is None:
        return _respuesta(502, {"error": "No se pudo autenticar con Google Drive (credenciales ausentes o vencidas). "
                                         "Avise al equipo técnico."})
    cuenta = drive.cuenta_respaldo(token)
    try:
        carpeta = drive.verificar_carpeta(folder_id, token)
    except drive.ErrorCarpeta as e:
        pista = f" Compártala como Editor con {cuenta}." if cuenta else ""
        return _respuesta(422, {"error": f"{e}{pista}"})
    except Exception as e:  # noqa: BLE001 -- Google caído o con error
        logger.exception("Drive: no se pudo verificar la carpeta %s: %s", folder_id, e)
        return _respuesta(502, {"error": "No se pudo consultar Google Drive. Intente de nuevo en unos minutos."})

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO configuracion (clave, valor, actualizado_por, actualizado_nombre, actualizado_en)
                VALUES ('drive', %s::jsonb, %s, %s, now())
                ON CONFLICT (clave) DO UPDATE
                SET valor = EXCLUDED.valor, actualizado_por = EXCLUDED.actualizado_por,
                    actualizado_nombre = EXCLUDED.actualizado_nombre, actualizado_en = now()
                RETURNING valor, actualizado_nombre, actualizado_en
                """,
                (json.dumps({"folder_id": carpeta["id"], "nombre": carpeta["nombre"]}),
                 auth.usuario_sub(event), auth.usuario_nombre(event)),
            )
            fila = cur.fetchone()
        conn.commit()
        return _respuesta(200, _estado(fila, cuenta))
    except Exception as e:
        conn.rollback()
        return _respuesta(500, {"error": str(e)})
    finally:
        conn.close()
