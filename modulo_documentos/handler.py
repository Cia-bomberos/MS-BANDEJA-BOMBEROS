"""
Módulo Bandeja Documental.

Implementa el ciclo de vida del documento descrito en RN-0004 a RN-0028 y
RF-0001 a RF-0009 del documento de análisis. El diseño de endpoints es
libre (no está fijado por el documento), así que se armó uno por cada
operación relevante del ciclo de vida.

Pendiente / fuera de alcance de esta primera versión (ver README):
- RN-0027 (subida automática a Google Drive al archivar): no hay cuenta de
  servicio de Google todavía. `modulo_documentos/scheduled.py` deja el punto
  de integración marcado con TODO para no bloquear el resto.
"""

import json
from datetime import date, datetime

from botocore.exceptions import ClientError

from modulo_documentos import auth, s3util
from modulo_documentos.codigo import formatear_codigo
from modulo_documentos.db import get_connection, siguiente_correlativo
from modulo_documentos.prioridad import calcular_prioridad

ESTADOS_VALIDOS = {"Pendiente", "En proceso", "Atendido", "Archivado"}
PRIORIDADES_VALIDAS = {"Alta", "Media", "Baja"}


def _respuesta(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _registrar_historial(cur, documento_id, event, accion: str, detalle: str = ""):
    cur.execute(
        """
        INSERT INTO historial_acciones (documento_id, usuario_sub, usuario_nombre, seccion, accion, detalle)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            documento_id,
            auth.usuario_sub(event),
            auth.usuario_nombre(event),
            auth.seccion_usuario(event) or auth.grupo(event),
            accion,
            detalle,
        ),
    )


def _obtener_seccion_doc(cur, documento_id) -> str | None:
    cur.execute("SELECT seccion_responsable FROM documentos WHERE id = %s", (documento_id,))
    row = cur.fetchone()
    return row["seccion_responsable"] if row else None


# ---------------------------------------------------------------------------
# POST /documentos/upload-url
# ---------------------------------------------------------------------------

def solicitar_url_subida(event, context):
    return _respuesta(200, s3util.generar_url_subida())


# ---------------------------------------------------------------------------
# POST /documentos
# ---------------------------------------------------------------------------

def crear_documento(event, context):
    """RN-0007, RN-0013, RN-0014, RN-0020, RN-0024, RN-0025, RF-0004, RF-0008."""
    if auth.es_jefatura(event):
        return _respuesta(403, {"error": "Jefatura no registra documentos directamente; debe hacerlo una sección."})

    seccion = auth.seccion_usuario(event)
    if not seccion:
        return _respuesta(403, {"error": "Rol no autorizado para registrar documentos."})

    body = json.loads(event.get("body") or "{}")
    origen = body.get("origen")  # "interno" | "externo" (RN-0024)
    archivo_s3_key = body.get("archivo_s3_key")
    fecha_limite_str = body.get("fecha_limite")
    tipo = body.get("tipo")
    prioridad_body = body.get("prioridad")

    if origen not in ("interno", "externo"):
        return _respuesta(400, {"error": "origen debe ser 'interno' o 'externo'."})
    if not archivo_s3_key or not fecha_limite_str:
        return _respuesta(400, {"error": "archivo_s3_key y fecha_limite son obligatorios."})

    if not s3util.validar_pdf(archivo_s3_key):
        return _respuesta(400, {"error": "El archivo no es un PDF válido o excede 20MB (RNF-0006/0007)."})

    try:
        fecha_limite = date.fromisoformat(fecha_limite_str)
    except ValueError:
        return _respuesta(400, {"error": "fecha_limite debe tener formato YYYY-MM-DD."})

    # RN-0024: simplificado para documentos externos, completo para internos.
    modalidad = "simplificado" if origen == "externo" else "completo"
    if modalidad == "completo" and not tipo:
        return _respuesta(400, {"error": "tipo es obligatorio para documentos internos (RN-0024)."})
    # RN-0025: simplificado exige prioridad/fecha, nunca código ni tipo.
    if modalidad == "simplificado":
        tipo = None

    prioridad_manual = prioridad_body in PRIORIDADES_VALIDAS
    prioridad = prioridad_body if prioridad_manual else calcular_prioridad(fecha_limite)

    conn = get_connection()
    try:
        codigo_unico = None
        if modalidad == "completo":
            anio = date.today().year
            numero = siguiente_correlativo(conn, tipo, anio)
            codigo_unico = formatear_codigo(tipo, numero, anio)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documentos (codigo_unico, tipo, modalidad, estado, prioridad, prioridad_manual,
                                         seccion_origen, seccion_responsable, fecha_limite, archivo_s3_key)
                VALUES (%s, %s, %s, 'Pendiente', %s, %s, %s, %s, %s, %s)
                RETURNING id, codigo_unico, tipo, modalidad, estado, prioridad, fecha_limite, fecha_creacion
                """,
                (codigo_unico, tipo, modalidad, prioridad, prioridad_manual, seccion, seccion, fecha_limite, archivo_s3_key),
            )
            doc = cur.fetchone()
            _registrar_historial(cur, doc["id"], event, "registro", f"Documento registrado ({modalidad}).")
        conn.commit()
        return _respuesta(201, doc)
    except Exception as e:
        conn.rollback()
        return _respuesta(500, {"error": str(e)})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /documentos
# ---------------------------------------------------------------------------

def listar_documentos(event, context):
    """RN-0004, RN-0005, RN-0006, RN-0019, RF-0002."""
    g = auth.grupo(event)
    params = event.get("queryStringParameters") or {}
    estado_filtro = params.get("estado")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if g in ("Jefatura", "Jefe_Administracion"):
                if estado_filtro:
                    cur.execute("SELECT * FROM documentos WHERE estado = %s ORDER BY fecha_creacion DESC", (estado_filtro,))
                else:
                    cur.execute("SELECT * FROM documentos ORDER BY fecha_creacion DESC")
            else:
                seccion = auth.seccion_usuario(event)
                if not seccion:
                    return _respuesta(403, {"error": "Rol no autorizado."})
                if estado_filtro:
                    cur.execute(
                        "SELECT * FROM documentos WHERE seccion_responsable = %s AND estado = %s ORDER BY fecha_creacion DESC",
                        (seccion, estado_filtro),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM documentos WHERE seccion_responsable = %s ORDER BY fecha_creacion DESC",
                        (seccion,),
                    )
            documentos = cur.fetchall()

        hoy = date.today()
        for doc in documentos:
            doc["vencido"] = doc["fecha_limite"] < hoy  # RN-0019

        return _respuesta(200, {"documentos": documentos})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /documentos/{id}
# ---------------------------------------------------------------------------

def obtener_documento(event, context):
    """RN-0006, RN-0012, RF-0003: detalle + historial completo."""
    documento_id = event["pathParameters"]["id"]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM documentos WHERE id = %s", (documento_id,))
            doc = cur.fetchone()
            if not doc:
                return _respuesta(404, {"error": "Documento no encontrado."})

            if not auth.puede_leer(event, doc["seccion_responsable"]):
                return _respuesta(403, {"error": "No tiene acceso a esta sección."})

            cur.execute(
                "SELECT * FROM historial_acciones WHERE documento_id = %s ORDER BY fecha_hora ASC",
                (documento_id,),
            )
            doc["historial"] = cur.fetchall()
            doc["vencido"] = doc["fecha_limite"] < date.today()

        return _respuesta(200, doc)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PATCH /documentos/{id}/derivar
# ---------------------------------------------------------------------------

def derivar_documento(event, context):
    """RN-0009, RN-0015, RF-0004: deriva a otra sección; pasa a 'En proceso'
    y el control de edición pasa a la sección receptora."""
    documento_id = event["pathParameters"]["id"]
    body = json.loads(event.get("body") or "{}")
    seccion_destino = body.get("seccion_destino")

    if seccion_destino not in auth.SECCIONES_VALIDAS:
        return _respuesta(400, {"error": "seccion_destino inválida."})

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            seccion_actual = _obtener_seccion_doc(cur, documento_id)
            if seccion_actual is None:
                return _respuesta(404, {"error": "Documento no encontrado."})
            if not auth.puede_escribir(event, seccion_actual):
                return _respuesta(403, {"error": "No tiene permiso para derivar este documento."})
            if seccion_destino == seccion_actual:
                return _respuesta(400, {"error": "El documento ya está en esa sección."})

            cur.execute(
                """
                UPDATE documentos
                SET seccion_responsable = %s, estado = 'En proceso', fecha_actualizacion = now()
                WHERE id = %s
                """,
                (seccion_destino, documento_id),
            )
            _registrar_historial(
                cur, documento_id, event, "derivacion",
                f"Derivado de {seccion_actual} a {seccion_destino}.",
            )
        conn.commit()
        return _respuesta(200, {"mensaje": "Documento derivado.", "seccion_responsable": seccion_destino})
    except Exception as e:
        conn.rollback()
        return _respuesta(500, {"error": str(e)})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PATCH /documentos/{id}/archivo
# ---------------------------------------------------------------------------

def actualizar_archivo(event, context):
    """RN-0010: solo la sección responsable actual puede reemplazar el
    adjunto mientras el documento esté 'En proceso'."""
    documento_id = event["pathParameters"]["id"]
    body = json.loads(event.get("body") or "{}")
    nuevo_s3_key = body.get("archivo_s3_key")

    if not nuevo_s3_key:
        return _respuesta(400, {"error": "archivo_s3_key es obligatorio."})
    if not s3util.validar_pdf(nuevo_s3_key):
        return _respuesta(400, {"error": "El archivo no es un PDF válido o excede 20MB."})

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT seccion_responsable, estado FROM documentos WHERE id = %s", (documento_id,))
            doc = cur.fetchone()
            if not doc:
                return _respuesta(404, {"error": "Documento no encontrado."})
            if doc["estado"] != "En proceso":
                return _respuesta(409, {"error": "Solo se puede reemplazar el archivo mientras el documento está 'En proceso'."})
            if not auth.puede_escribir(event, doc["seccion_responsable"]):
                return _respuesta(403, {"error": "No tiene permiso sobre este documento."})

            cur.execute(
                "UPDATE documentos SET archivo_s3_key = %s, fecha_actualizacion = now() WHERE id = %s",
                (nuevo_s3_key, documento_id),
            )
            _registrar_historial(cur, documento_id, event, "actualizacion_archivo", "Archivo adjunto reemplazado.")
        conn.commit()
        return _respuesta(200, {"mensaje": "Archivo actualizado."})
    except Exception as e:
        conn.rollback()
        return _respuesta(500, {"error": str(e)})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PATCH /documentos/{id}/atender
# ---------------------------------------------------------------------------

def marcar_atendido(event, context):
    """RN-0008, RN-0011: pasa a 'Atendido' (desde 'Pendiente' -- simplificado --
    o desde 'En proceso' cuando la sección que lo revisó cierra la gestión)."""
    documento_id = event["pathParameters"]["id"]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT seccion_responsable, estado FROM documentos WHERE id = %s", (documento_id,))
            doc = cur.fetchone()
            if not doc:
                return _respuesta(404, {"error": "Documento no encontrado."})
            if doc["estado"] not in ("Pendiente", "En proceso"):
                return _respuesta(409, {"error": f"No se puede marcar como Atendido desde el estado '{doc['estado']}'."})
            if not auth.puede_escribir(event, doc["seccion_responsable"]):
                return _respuesta(403, {"error": "No tiene permiso sobre este documento."})

            cur.execute(
                "UPDATE documentos SET estado = 'Atendido', atendido_en = now(), fecha_actualizacion = now() WHERE id = %s",
                (documento_id,),
            )
            _registrar_historial(cur, documento_id, event, "atendido", "Documento marcado como Atendido.")
        conn.commit()
        return _respuesta(200, {"mensaje": "Documento marcado como Atendido."})
    except Exception as e:
        conn.rollback()
        return _respuesta(500, {"error": str(e)})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PATCH /documentos/{id}/prioridad
# ---------------------------------------------------------------------------

def asignar_prioridad(event, context):
    """RN-0018, RF-0005: asignación manual de prioridad y/o fecha límite."""
    documento_id = event["pathParameters"]["id"]
    body = json.loads(event.get("body") or "{}")
    prioridad = body.get("prioridad")
    fecha_limite_str = body.get("fecha_limite")

    if prioridad is not None and prioridad not in PRIORIDADES_VALIDAS:
        return _respuesta(400, {"error": "prioridad debe ser Alta, Media o Baja."})

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT seccion_responsable FROM documentos WHERE id = %s", (documento_id,))
            doc = cur.fetchone()
            if not doc:
                return _respuesta(404, {"error": "Documento no encontrado."})
            if not auth.puede_escribir(event, doc["seccion_responsable"]):
                return _respuesta(403, {"error": "No tiene permiso sobre este documento."})

            campos, valores = [], []
            if prioridad is not None:
                campos += ["prioridad = %s", "prioridad_manual = true"]
                valores.append(prioridad)
            if fecha_limite_str is not None:
                try:
                    fecha_limite = date.fromisoformat(fecha_limite_str)
                except ValueError:
                    return _respuesta(400, {"error": "fecha_limite debe tener formato YYYY-MM-DD."})
                campos.append("fecha_limite = %s")
                valores.append(fecha_limite)

            if not campos:
                return _respuesta(400, {"error": "Debe enviar prioridad y/o fecha_limite."})

            valores.append(documento_id)
            cur.execute(f"UPDATE documentos SET {', '.join(campos)}, fecha_actualizacion = now() WHERE id = %s", valores)
            _registrar_historial(cur, documento_id, event, "prioridad_manual", f"Ajuste manual: {body}.")
        conn.commit()
        return _respuesta(200, {"mensaje": "Prioridad/fecha límite actualizada."})
    except Exception as e:
        conn.rollback()
        return _respuesta(500, {"error": str(e)})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POST /documentos/{id}/envio-externo
# ---------------------------------------------------------------------------

def registrar_envio_externo(event, context):
    """RN-0021, RN-0022, RF-0007: registra el envío manual a una entidad
    externa y cierra automáticamente la gestión como 'Atendido'."""
    documento_id = event["pathParameters"]["id"]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT seccion_responsable, estado FROM documentos WHERE id = %s", (documento_id,))
            doc = cur.fetchone()
            if not doc:
                return _respuesta(404, {"error": "Documento no encontrado."})
            if doc["estado"] == "Archivado":
                return _respuesta(409, {"error": "El documento ya está Archivado."})
            if not auth.puede_escribir(event, doc["seccion_responsable"]):
                return _respuesta(403, {"error": "No tiene permiso sobre este documento."})

            cur.execute(
                "UPDATE documentos SET estado = 'Atendido', atendido_en = now(), fecha_actualizacion = now() WHERE id = %s",
                (documento_id,),
            )
            _registrar_historial(cur, documento_id, event, "envio_externo", "Envío externo registrado; documento Atendido.")
        conn.commit()
        return _respuesta(200, {"mensaje": "Envío externo registrado. Documento marcado como Atendido."})
    except Exception as e:
        conn.rollback()
        return _respuesta(500, {"error": str(e)})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /documentos/{id}/descargar
# ---------------------------------------------------------------------------

def descargar_documento(event, context):
    """RN-0023: URL pre-firmada para descargar el documento en su formato original."""
    documento_id = event["pathParameters"]["id"]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT seccion_responsable, archivo_s3_key FROM documentos WHERE id = %s", (documento_id,))
            doc = cur.fetchone()
            if not doc:
                return _respuesta(404, {"error": "Documento no encontrado."})
            if not auth.puede_leer(event, doc["seccion_responsable"]):
                return _respuesta(403, {"error": "No tiene acceso a esta sección."})

        url = s3util.generar_url_descarga(doc["archivo_s3_key"])
        return _respuesta(200, {"downloadUrl": url})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# DELETE /documentos/{id}
# ---------------------------------------------------------------------------

def eliminar_documento(event, context):
    """RN-0028, RF-0009: solo Jefe_Administracion, solo si está Archivado y
    ya se confirmó la subida a Drive."""
    documento_id = event["pathParameters"]["id"]

    if not auth.es_jefe_administracion(event):
        return _respuesta(403, {"error": "Solo el Jefe de Administración puede eliminar documentos archivados."})

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT estado, confirmado_drive FROM documentos WHERE id = %s", (documento_id,))
            doc = cur.fetchone()
            if not doc:
                return _respuesta(404, {"error": "Documento no encontrado."})
            if doc["estado"] != "Archivado":
                return _respuesta(409, {"error": "Solo se pueden eliminar documentos en estado Archivado."})
            if not doc["confirmado_drive"]:
                return _respuesta(409, {"error": "No se puede eliminar: todavía no se confirmó la subida a Google Drive."})

            cur.execute("DELETE FROM historial_acciones WHERE documento_id = %s", (documento_id,))
            cur.execute("DELETE FROM documentos WHERE id = %s", (documento_id,))
        conn.commit()
        return _respuesta(200, {"mensaje": "Documento eliminado."})
    except Exception as e:
        conn.rollback()
        return _respuesta(500, {"error": str(e)})
    finally:
        conn.close()
