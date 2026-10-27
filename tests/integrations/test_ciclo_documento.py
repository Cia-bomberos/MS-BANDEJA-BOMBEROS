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
            "archivo_s3_key": subir(contenido), "asunto": "prueba"
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

    monkeypatch.setattr(  # Drive real no se prueba aqui
        scheduled, "_subir_a_drive", lambda *a: {"file_id": "drv-1", "folder_id": "mes-1"}
    )
    assert scheduled.archivar_documentos({}, None)["archivados"] == 1
    fila = consulta(
        "SELECT estado, confirmado_drive, drive_file_id, drive_folder_id, respaldado_en FROM documentos WHERE id = %s",
        (doc["id"],),
    )[0]
    assert fila["estado"] == "Archivado"
    assert fila["confirmado_drive"] is True
    assert (fila["drive_file_id"], fila["drive_folder_id"]) == ("drv-1", "mes-1")
    assert fila["respaldado_en"] is not None

    assert llamar(handler.eliminar_documento, "Jefe_Maquinas", id=doc["id"])[0] == 403
    assert llamar(handler.eliminar_documento, "Jefe_Administracion", id=doc["id"])[0] == 200
    assert consulta("SELECT count(*) AS n FROM documentos")[0]["n"] == 0


# --- Carpeta de Drive configurable (RN-0027) ------------------------------------------

ID_CARPETA = "1AbCdEfGhIjKlMnOpQrStUvWxYz_-0123"


@pytest.fixture
def drive_falso(monkeypatch):
    from modulo_documentos import drive
    monkeypatch.setattr(drive, "_token", lambda s3=None: "tok")
    monkeypatch.setattr(drive, "cuenta_respaldo", lambda token: "respaldo@gmail.com")
    monkeypatch.setattr(drive, "verificar_carpeta", lambda fid, token: {"id": fid, "nombre": "SOFTWARE_BOMBEROS"})


def test_la_migracion_002_se_puede_aplicar_dos_veces(bd):
    import pathlib
    sql = (pathlib.Path(__file__).resolve().parents[2] / "migrations" / "002_drive.sql").read_text(encoding="utf-8")
    with bd.cursor() as cur:
        cur.execute(sql)
        cur.execute(sql)


def test_configurar_carpeta_la_guarda_y_el_archivador_la_usa(bd, registrar, llamar, consulta, drive_falso, monkeypatch):
    from modulo_documentos import configuracion
    enlace = f"https://drive.google.com/drive/folders/{ID_CARPETA}?usp=sharing"

    # Solo el Jefe de Administración puede
    assert llamar(configuracion.guardar_config_drive, "Jefatura", body={"enlace": enlace})[0] == 403
    estado, cuerpo = llamar(configuracion.guardar_config_drive, "Jefe_Administracion", body={"enlace": enlace})
    assert estado == 200
    assert cuerpo["carpeta_id"] == ID_CARPETA
    estado, cuerpo = llamar(configuracion.obtener_config_drive, "Jefe_Administracion")
    assert (estado, cuerpo["carpeta_nombre"], cuerpo["origen"]) == (200, "SOFTWARE_BOMBEROS", "configuracion")

    # Actualizar reemplaza la fila (no la duplica)
    otro = "1ZyXwVuTsRqPoNmLkJiHgFeDcBa_-9876"
    assert llamar(configuracion.guardar_config_drive, "Jefe_Administracion", body={"enlace": otro})[0] == 200
    assert consulta("SELECT count(*) AS n FROM configuracion")[0]["n"] == 1

    # El archivador respalda en la carpeta configurada, con la fecha de registro del documento
    doc = registrar()
    assert llamar(handler.marcar_atendido, "Jefe_Maquinas", id=doc["id"])[0] == 200
    consulta("UPDATE documentos SET atendido_en = now() - interval '4 days' WHERE id = %s", (doc["id"],))
    vistas = {}

    def _respaldar(documento_id, codigo, key, fecha=None, raiz=None, **k):
        vistas.update(raiz=raiz, fecha=fecha)
        return {"file_id": "drv-1", "folder_id": "mes-1"}

    monkeypatch.setattr(scheduled.drive, "respaldar", _respaldar)
    assert scheduled.archivar_documentos({}, None)["archivados"] == 1
    assert vistas["raiz"] == otro
    registro = consulta("SELECT fecha_creacion FROM documentos WHERE id = %s", (doc["id"],))[0]["fecha_creacion"]
    assert vistas["fecha"] == registro
