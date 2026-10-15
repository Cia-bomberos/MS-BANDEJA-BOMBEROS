from datetime import timedelta

import pytest

from modulo_documentos import handler, scheduled
from modulo_documentos.tiempo import hoy_lima

pytestmark = pytest.mark.integration

TIPO = "Oficio"  # ajusta a un tipo valido de modulo_documentos/codigo.py
ENVIO = {"medio": "Correo", "destinatario": "Municipalidad de Lima",
         "fecha_envio": "2020-01-15", "hora_envio": "14:30"}


@pytest.fixture
def registrar(llamar, subir):
    def _registrar(grupo="Jefe_Maquinas", dias=60, contenido=None):
        status, doc = llamar(handler.crear_documento, grupo, body={
            "origen": "interno", "tipo": TIPO,
            "fecha_limite": (hoy_lima() + timedelta(days=dias)).isoformat(),
            "archivo_s3_key": subir(contenido),
        })
        assert status == 201, doc
        return doc
    return _registrar


def test_ciclo_registro_derivacion_atencion(bd, registrar, llamar, consulta):
    doc = registrar()
    assert doc["estado"] == "Pendiente"
    assert doc["codigo_unico"]

    fila = consulta("SELECT archivo_s3_key FROM documentos WHERE id = %s", (doc["id"],))[0]
    assert fila["archivo_s3_key"].startswith("documentos/")  # promovido desde pendientes/

    status, _ = llamar(handler.derivar_documento, "Jefe_Maquinas", id=doc["id"],
                       body={"seccion_destino": "Sanidad"})
    assert status == 200
    # Maquinas ya no controla el documento; Sanidad si
    assert llamar(handler.marcar_atendido, "Jefe_Maquinas", id=doc["id"])[0] == 403
    assert llamar(handler.marcar_atendido, "Jefe_Sanidad", id=doc["id"])[0] == 200

    acciones = [r["accion"] for r in consulta(
        "SELECT accion FROM historial_acciones WHERE documento_id = %s ORDER BY fecha_hora", (doc["id"],))]
    assert acciones == ["registro", "derivacion", "atendido"]


def test_correlativos_distintos(bd, registrar):
    a, b = registrar(), registrar()
    assert a["codigo_unico"] != b["codigo_unico"]


def test_permisos_por_seccion(bd, registrar, llamar):
    doc = registrar()
    assert llamar(handler.obtener_documento, "Jefe_Sanidad", id=doc["id"])[0] == 403
    assert llamar(handler.obtener_documento, "Jefe_Maquinas", id=doc["id"])[0] == 200
    assert llamar(handler.obtener_documento, "Jefatura", id=doc["id"])[0] == 200


def test_pdf_falso_se_rechaza_y_no_deja_registro(bd, llamar, subir, consulta):
    key = subir(b"%PDF-" + b"\x00\xffbasura" * 50)
    status, _ = llamar(handler.crear_documento, "Jefe_Maquinas", body={
        "origen": "interno", "tipo": TIPO,
        "fecha_limite": (hoy_lima() + timedelta(days=30)).isoformat(),
        "archivo_s3_key": key,
    })
    assert status == 400
    assert consulta("SELECT count(*) AS n FROM documentos")[0]["n"] == 0


def test_envio_externo_exige_descarga_y_guarda_los_datos(bd, registrar, llamar, consulta):
    doc = registrar()
    assert llamar(handler.registrar_envio_externo, "Jefe_Maquinas", id=doc["id"], body=ENVIO)[0] == 409

    assert llamar(handler.descargar_documento, "Jefe_Maquinas", id=doc["id"])[0] == 200
    assert llamar(handler.registrar_envio_externo, "Jefe_Maquinas", id=doc["id"], body=ENVIO)[0] == 200

    fila = consulta("SELECT detalle FROM historial_acciones WHERE documento_id = %s AND accion = 'envio_externo'",
                    (doc["id"],))[0]
    assert "Municipalidad de Lima" in fila["detalle"]
    assert "14:30" in fila["detalle"]


def test_reclasificacion_horaria(bd, registrar, consulta):
    doc = registrar(dias=60)
    assert consulta("SELECT prioridad FROM documentos WHERE id = %s", (doc["id"],))[0]["prioridad"] == "Baja"

    consulta("UPDATE documentos SET fecha_limite = %s WHERE id = %s",
             (hoy_lima() + timedelta(days=3), doc["id"]))
    assert scheduled.reclasificar_prioridades({}, None) == {"actualizados": 1}
    assert consulta("SELECT prioridad FROM documentos WHERE id = %s", (doc["id"],))[0]["prioridad"] == "Alta"

def test_archivado_automatico_y_eliminacion(bd, registrar, llamar, consulta, monkeypatch):
    doc = registrar()
    assert llamar(handler.marcar_atendido, "Jefe_Maquinas", id=doc["id"])[0] == 200
    consulta("UPDATE documentos SET atendido_en = now() - interval '4 days' WHERE id = %s", (doc["id"],))

    monkeypatch.setattr(scheduled, "_subir_a_drive", lambda *a: True)  # Drive real no se prueba aqui
    assert scheduled.archivar_documentos({}, None)["archivados"] == 1
    fila = consulta("SELECT estado, confirmado_drive FROM documentos WHERE id = %s", (doc["id"],))[0]
    assert fila["estado"] == "Archivado"
    assert fila["confirmado_drive"] is True

    assert llamar(handler.eliminar_documento, "Jefe_Maquinas", id=doc["id"])[0] == 403
    assert llamar(handler.eliminar_documento, "Jefe_Administracion", id=doc["id"])[0] == 200
    assert consulta("SELECT count(*) AS n FROM documentos")[0]["n"] == 0
