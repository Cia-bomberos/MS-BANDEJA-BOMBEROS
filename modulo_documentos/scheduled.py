"""
Jobs programados (CloudWatch Events / EventBridge, ver serverless.yml).
No tienen event de API Gateway: se disparan por cron, no por HTTP.
"""

import logging
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


def _subir_a_drive(documento_id, codigo_unico, archivo_s3_key) -> bool:
    """RN-0027 / RNF-0009: respalda el PDF en Google Drive. True solo si Drive lo
    confirmó; si falla o no está configurado devuelve False y el documento se
    reintenta en la siguiente corrida (ver `archivar_documentos`)."""
    return drive.respaldar(documento_id, codigo_unico, archivo_s3_key)


def archivar_documentos(event, context):
    """RN-0026: 'Atendido' -> 'Archivado' tras 3 días. RN-0027: al archivar,
    respalda en Google Drive. Si el respaldo falla, el documento se archiva
    igual (RN-0026) pero queda con confirmado_drive = false y se reintenta en
    cada corrida hasta que Drive lo confirme (RN-0028 exige esa confirmación
    para poder eliminarlo)."""
    conn = get_connection()
    archivados = 0
    reintentados = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, codigo_unico, archivo_s3_key FROM documentos
                WHERE estado = 'Atendido' AND atendido_en <= now() - interval '3 days'
                """
            )
            por_archivar = cur.fetchall()

            for doc in por_archivar:
                confirmado = _subir_a_drive(doc["id"], doc["codigo_unico"], doc["archivo_s3_key"])
                cur.execute(
                    """
                    UPDATE documentos
                    SET estado = 'Archivado', confirmado_drive = %s, fecha_actualizacion = now()
                    WHERE id = %s
                    """,
                    (confirmado, doc["id"]),
                )
                conn.commit()  # por documento: un timeout no pierde lo ya procesado
                archivados += 1

            cur.execute(
                """
                SELECT id, codigo_unico, archivo_s3_key FROM documentos
                WHERE estado = 'Archivado' AND confirmado_drive = false
                """
            )
            pendientes = cur.fetchall()

            for doc in pendientes:
                if _subir_a_drive(doc["id"], doc["codigo_unico"], doc["archivo_s3_key"]):
                    cur.execute(
                        "UPDATE documentos SET confirmado_drive = true, fecha_actualizacion = now() WHERE id = %s",
                        (doc["id"],),
                    )
                    conn.commit()
                    reintentados += 1
        logger.info("Archivado automático: %s archivados, %s respaldos pendientes confirmados.", archivados, reintentados)
        return {"archivados": archivados, "respaldos_reintentados": reintentados}
    finally:
        conn.close()
