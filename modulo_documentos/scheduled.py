"""
Jobs programados (CloudWatch Events / EventBridge, ver serverless.yml).
No tienen event de API Gateway: se disparan por cron, no por HTTP.
"""

import logging
from datetime import date

from modulo_documentos.db import get_connection
from modulo_documentos.prioridad import reclasificar

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def reclasificar_prioridades(event, context):
    """RN-0017: reevalúa periódicamente la prioridad de los documentos en
    estado 'Pendiente'. Una vez que el documento pasa a 'En proceso' deja
    de reclasificarse (conserva la prioridad vigente, según RN-0017)."""
    hoy = date.today()
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
                    actualizados += 1
        conn.commit()
        logger.info("Reclasificación de prioridades: %s documentos actualizados.", actualizados)
        return {"actualizados": actualizados}
    finally:
        conn.close()


def _subir_a_drive(documento_id, archivo_s3_key) -> bool:
    """TODO: integrar con la API de Google Drive (RN-0027/RF-0009) en cuanto
    el equipo tenga la cuenta de servicio de Google Cloud y el ID de la
    carpeta de la Compañía. Por ahora es un stub que NO confirma nada, para
    que `confirmado_drive` nunca se marque en falso-positivo mientras esta
    pieza no esté implementada de verdad.

    Cuando se implemente: exportar/copiar el PDF desde
    s3://<DOCUMENTS_BUCKET>/<archivo_s3_key> a la carpeta de Drive vía la
    API, con manejo de errores/timeouts (RNF-0009) -- si falla, NO debe
    marcarse confirmado_drive y el documento se reintenta en la siguiente
    corrida de este job.
    """
    logger.warning(
        "Subida a Google Drive no implementada todavía (documento %s, key %s). "
        "Pendiente: cuenta de servicio de Google Cloud.",
        documento_id, archivo_s3_key,
    )
    return False


def archivar_documentos(event, context):
    """RN-0026: 'Atendido' -> 'Archivado' tras 3 días. RN-0027: al archivar,
    exportar a Google Drive (ver limitación del stub arriba)."""
    conn = get_connection()
    archivados = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, archivo_s3_key FROM documentos
                WHERE estado = 'Atendido' AND atendido_en <= now() - interval '3 days'
                """
            )
            documentos = cur.fetchall()

            for doc in documentos:
                confirmado = _subir_a_drive(doc["id"], doc["archivo_s3_key"])
                cur.execute(
                    """
                    UPDATE documentos
                    SET estado = 'Archivado', confirmado_drive = %s, fecha_actualizacion = now()
                    WHERE id = %s
                    """,
                    (confirmado, doc["id"]),
                )
                archivados += 1
        conn.commit()
        logger.info("Archivado automático: %s documentos procesados.", archivados)
        return {"archivados": archivados}
    finally:
        conn.close()
