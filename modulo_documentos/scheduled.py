"""
Jobs programados (CloudWatch Events / EventBridge, ver serverless.yml).
No tienen event de API Gateway: se disparan por cron, no por HTTP.
"""

import logging
import os

from modulo_documentos import drive
from modulo_documentos.db import get_connection
from modulo_documentos.prioridad import dias_restantes, reclasificar
from modulo_documentos.tiempo import hoy_lima

logger = logging.getLogger()
logger.setLevel(logging.INFO)

USUARIO_SISTEMA = "sistema"  # autor de las acciones automáticas en historial_acciones


def reclasificar_prioridades(event, context):
    """RN-0017: reevalúa periódicamente la prioridad de los documentos en
    estado 'Pendiente'. Una vez que el documento pasa a 'En proceso' deja
    de reclasificarse (conserva la prioridad vigente, según RN-0017)."""
    hoy = hoy_lima()  # día de Lima, no el UTC del servidor
    conn = get_connection()
    actualizados = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, prioridad, prioridad_manual, fecha_limite FROM documentos WHERE estado = 'Pendiente'"
            )
            documentos = cur.fetchall()

            for doc in documentos:
                nueva = reclasificar(doc["prioridad"], doc["prioridad_manual"], doc["fecha_limite"], hoy)
                if nueva != doc["prioridad"]:
                    cur.execute(
                        "UPDATE documentos SET prioridad = %s, fecha_actualizacion = now() WHERE id = %s",
                        (nueva, doc["id"]),
                    )
                    # RN-0014 / CU-006: toda reclasificación automática queda en el historial.
                    cur.execute(
                        """
                        INSERT INTO historial_acciones
                            (documento_id, usuario_sub, usuario_nombre, seccion, accion, detalle)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            doc["id"], USUARIO_SISTEMA, "Sistema (reclasificación automática)", "Sistema",
                            "reclasificacion_automatica",
                            f"Prioridad reclasificada de {doc['prioridad']} a {nueva}: "
                            f"quedan {dias_restantes(doc['fecha_limite'], hoy)} día(s) para la fecha límite.",
                        ),
                    )
                    actualizados += 1
        conn.commit()
        logger.info("Reclasificación de prioridades: %s documentos actualizados.", actualizados)
        return {"actualizados": actualizados}
    finally:
        conn.close()


def _carpeta_raiz(cur):
    """Carpeta raíz del respaldo: la que el Jefe de Administración configuró en el front;
    si nunca se configuró, la variable DRIVE_FOLDER_ID del deploy."""
    cur.execute("SELECT valor FROM configuracion WHERE clave = 'drive'")
    fila = cur.fetchone()
    if fila and fila["valor"].get("folder_id"):
        return fila["valor"]["folder_id"]
    return os.environ.get("DRIVE_FOLDER_ID", "").strip() or None


def _subir_a_drive(doc, raiz):
    """RN-0027 / RNF-0009: respalda el PDF en Google Drive (`<raíz>/<Año>/<Mes>/`, según la
    fecha de registro). Devuelve {"file_id", "folder_id"} solo si Drive lo confirmó; si
    falla o no está configurado devuelve None y el documento se reintenta en la
    siguiente corrida (ver `archivar_documentos`)."""
    return drive.respaldar(
        doc["id"], doc["codigo_unico"], doc["archivo_s3_key"], fecha=doc["fecha_creacion"], raiz=raiz
    )


def _respaldar_doc(cur, doc):
    """Sube un documento a Drive y actualiza la fila. Devuelve True si Drive confirmó.
    NO hace commit: lo decide el llamador (por documento)."""
    raiz = _carpeta_raiz(cur)
    respaldo = _subir_a_drive(doc, raiz)
    cur.execute(
        """
        UPDATE documentos
        SET confirmado_drive = %s, drive_file_id = %s, drive_folder_id = %s,
            respaldado_en = CASE WHEN %s THEN now() ELSE NULL END,
            fecha_actualizacion = now()
        WHERE id = %s
        """,
        (bool(respaldo), (respaldo or {}).get("file_id"), (respaldo or {}).get("folder_id"),
         bool(respaldo), doc["id"]),
    )
    return bool(respaldo)


def _ejecutar_archivado(conn):
    """Lógica compartida por el cron y el endpoint manual. Devuelve
    (archivados, reintentados) contando solo los que quedaron confirmados en Drive."""
    archivados = 0
    reintentados = 0
    with conn.cursor() as cur:
        # 1) Atendido con más de 3 días -> Archivado + intento de respaldo
        cur.execute(
            """
            SELECT id, codigo_unico, archivo_s3_key, fecha_creacion FROM documentos
            WHERE estado = 'Atendido' AND atendido_en <= now() - interval '3 days'
            """
        )
        por_archivar = cur.fetchall()

        for doc in por_archivar:
            _respaldar_doc(cur, doc)
            cur.execute(
                "UPDATE documentos SET estado = 'Archivado', fecha_actualizacion = now() WHERE id = %s",
                (doc["id"],),
            )
            conn.commit()
            archivados += 1

        # 2) Archivado con confirmado_drive = false -> reintentar
        cur.execute(
            """
            SELECT id, codigo_unico, archivo_s3_key, fecha_creacion FROM documentos
            WHERE estado = 'Archivado' AND confirmado_drive = false
            """
        )
        pendientes = cur.fetchall()

        for doc in pendientes:
            if _respaldar_doc(cur, doc):
                conn.commit()
                reintentados += 1
    return archivados, reintentados


def archivar_documentos(event, context):
    """Cron: misma lógica que el botón manual."""
    conn = get_connection()
    try:
        archivados, reintentados = _ejecutar_archivado(conn)
        logger.info(
            "Archivado automático: %s archivados, %s respaldos pendientes confirmados.",
            archivados, reintentados,
        )
        return {"archivados": archivados, "respaldos_reintentados": reintentados}
    finally:
        conn.close()


def respaldar_un_documento(event, context):
    """Invocada desde archivar_documento. `event` = {"documento_id": "..."}.
    Respalda SOLO ese documento. No cambia el estado."""
    documento_id = event.get("documento_id")
    if not documento_id:
        return {"ok": False, "error": "documento_id requerido"}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, codigo_unico, archivo_s3_key, fecha_creacion
                   FROM documentos WHERE id = %s""",
                (documento_id,),
            )
            doc = cur.fetchone()
            if not doc:
                return {"ok": False, "error": "documento no encontrado"}

            confirmado = _respaldar_doc(cur, doc)
        conn.commit()
        return {"ok": confirmado}
    except Exception as e:
        conn.rollback()
        logger.exception("Error respaldando documento %s: %s", documento_id, e)
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()