"""
Tests adicionales de modulo_documentos/handler.py: endpoints que
test_handler.py no cubría (detalle, archivo, atender, prioridad, envío
externo, descarga, URL de subida) y sus ramas de error. BD y S3 mockeados.
"""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock
from modulo_documentos.tiempo import hoy_lima

import pytest

from modulo_documentos import handler


def _event(grupo="", path_params=None, body=None, query=None):
    ev = {"requestContext": {"authorizer": {"claims": {"cognito:groups": grupo, "sub": "u-1", "name": "Tester"}}}}
    if path_params is not None:
        ev["pathParameters"] = path_params
    if body is not None:
        ev["body"] = json.dumps(body)
    if query is not None:
        ev["queryStringParameters"] = query
    return ev


class _CursorCtx:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, *exc):
        return False


@pytest.fixture
def conn_mock(monkeypatch):
    cursor = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = _CursorCtx(cursor)
    monkeypatch.setattr(handler, "get_connection", lambda: conn)
    return conn, cursor


@pytest.fixture(autouse=True)
def s3_valido(monkeypatch):
    """Por defecto, cualquier archivo_s3_key se valida como PDF OK y se 'promueve'
    a su key definitiva. Se registra qué keys se eliminaron de S3."""
    monkeypatch.setattr(handler.s3util, "promover_pdf", lambda key: key.replace("pendientes/", "documentos/"))
    eliminados = []
    monkeypatch.setattr(handler.s3util, "eliminar", eliminados.append)
    return eliminados


ID = {"id": "doc-1"}


def _doc(seccion="Maquinas", estado="En proceso", **extra):
    return {"seccion_responsable": seccion, "estado": estado, **extra}


# ---------------------------------------------------------------------------
class TestSolicitarUrlSubida:
    def test_devuelve_la_url_y_la_key(self, monkeypatch):
        monkeypatch.setattr(
            handler.s3util, "generar_url_subida",
            lambda tamano_bytes=None: {"uploadUrl": "https://s3/x", "archivo_s3_key": "pendientes/a.pdf", "expiraEn": 300},
        )
        resp = handler.solicitar_url_subida(_event("Jefe_Sanidad", body={}), None)
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["archivo_s3_key"] == "pendientes/a.pdf"

    def test_firma_el_tamano_cuando_el_cliente_lo_informa(self, monkeypatch):
        recibido = {}

        def falso(tamano_bytes=None):
            recibido["tamano"] = tamano_bytes
            return {"uploadUrl": "u", "archivo_s3_key": "pendientes/a.pdf", "expiraEn": 300}

        monkeypatch.setattr(handler.s3util, "generar_url_subida", falso)
        resp = handler.solicitar_url_subida(_event("Jefe_Sanidad", body={"tamano_bytes": 1234}), None)
        assert resp["statusCode"] == 200 and recibido["tamano"] == 1234

    @pytest.mark.parametrize("tamano", [0, -5, "grande", True, 1.5])
    def test_tamano_invalido(self, tamano):
        resp = handler.solicitar_url_subida(_event("Jefe_Sanidad", body={"tamano_bytes": tamano}), None)
        assert resp["statusCode"] == 400

    def test_rechaza_archivos_de_mas_de_20mb_sin_generar_url(self, monkeypatch):
        monkeypatch.setattr(handler.s3util, "generar_url_subida", lambda *a, **k: pytest.fail("no debe firmar"))
        resp = handler.solicitar_url_subida(_event("Jefe_Sanidad", body={"tamano_bytes": 21 * 1024 * 1024}), None)
        assert resp["statusCode"] == 400


# ---------------------------------------------------------------------------
class TestObtenerDocumento:
    def test_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.obtener_documento(_event("Jefatura", ID), None)["statusCode"] == 404

    def test_otra_seccion_que_nunca_participo_no_puede_leer(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.side_effect = [{"seccion_responsable": "Sanidad", "fecha_limite": hoy_lima()}, None]
        assert handler.obtener_documento(_event("Jefe_Maquinas", ID), None)["statusCode"] == 403

    def test_la_seccion_que_derivo_conserva_la_consulta_en_solo_lectura(self, conn_mock):
        """RN-0021 / CU-008: la sección de origen sigue viendo el documento que derivó."""
        _, cur = conn_mock
        cur.fetchone.side_effect = [
            {"seccion_responsable": "Administracion", "fecha_limite": hoy_lima() + timedelta(days=5)},
            {"?column?": 1},  # el historial muestra que Sanidad actuó sobre el documento
        ]
        cur.fetchall.return_value = [{"accion": "derivacion"}]
        resp = handler.obtener_documento(_event("Jefe_Sanidad", ID), None)
        body = json.loads(resp["body"])
        assert resp["statusCode"] == 200
        assert body["solo_lectura"] is True

    def test_incluye_historial_y_marca_vencido(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Maquinas", "fecha_limite": hoy_lima() - timedelta(days=2)}
        cur.fetchall.return_value = [{"accion": "registro"}]
        resp = handler.obtener_documento(_event("Jefe_Maquinas", ID), None)
        body = json.loads(resp["body"])
        assert resp["statusCode"] == 200
        assert body["historial"] == [{"accion": "registro"}]
        assert body["vencido"] is True
        assert body["solo_lectura"] is False


# ---------------------------------------------------------------------------
class TestListarConFiltro:
    def test_jefatura_filtra_por_estado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchall.return_value = []
        resp = handler.listar_documentos(_event("Jefatura", query={"estado": "Pendiente"}), None)
        assert resp["statusCode"] == 200
        sql, params = cur.execute.call_args.args
        assert "estado = %s" in sql
        assert params == ("Pendiente",)

    def test_seccion_incluye_lo_que_registro_o_derivo_y_lo_marca_solo_lectura(self, conn_mock):
        _, cur = conn_mock
        cur.fetchall.return_value = [
            {"seccion_responsable": "Sanidad", "fecha_limite": hoy_lima() + timedelta(days=3)},
            {"seccion_responsable": "Maquinas", "fecha_limite": hoy_lima() + timedelta(days=3)},
        ]
        resp = handler.listar_documentos(_event("Jefe_Sanidad", query={"estado": "En proceso"}), None)
        sql, params = cur.execute.call_args.args
        assert "historial_acciones" in sql
        assert params == ("Sanidad", "Sanidad", "En proceso")
        docs = json.loads(resp["body"])["documentos"]
        assert [d["solo_lectura"] for d in docs] == [False, True]


# ---------------------------------------------------------------------------
class TestActualizarArchivo:
    def test_requiere_key(self, conn_mock):
        assert handler.actualizar_archivo(_event("Jefe_Maquinas", ID, body={}), None)["statusCode"] == 400

    def test_pdf_invalido(self, conn_mock, monkeypatch):
        conn, cur = conn_mock
        cur.fetchone.return_value = _doc()
        monkeypatch.setattr(handler.s3util, "promover_pdf", lambda k: None)
        assert handler.actualizar_archivo(_event("Jefe_Maquinas", ID, body={"archivo_s3_key": "k"}), None)["statusCode"] == 400
        conn.commit.assert_not_called()

    def test_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.actualizar_archivo(_event("Jefe_Maquinas", ID, body={"archivo_s3_key": "k"}), None)["statusCode"] == 404

    def test_solo_en_proceso(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(estado="Pendiente")
        assert handler.actualizar_archivo(_event("Jefe_Maquinas", ID, body={"archivo_s3_key": "k"}), None)["statusCode"] == 409

    def test_sin_permiso(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(seccion="Sanidad")
        assert handler.actualizar_archivo(_event("Jefe_Maquinas", ID, body={"archivo_s3_key": "k"}), None)["statusCode"] == 403

    def test_exito_elimina_el_archivo_anterior(self, conn_mock, s3_valido):
        conn, cur = conn_mock
        cur.fetchone.return_value = _doc(archivo_s3_key="documentos/viejo.pdf")
        resp = handler.actualizar_archivo(_event("Jefe_Maquinas", ID, body={"archivo_s3_key": "pendientes/nuevo.pdf"}), None)
        assert resp["statusCode"] == 200
        conn.commit.assert_called_once()
        assert s3_valido == ["documentos/viejo.pdf"]

    def test_error_de_bd_elimina_el_archivo_nuevo_y_conserva_el_anterior(self, conn_mock, s3_valido):
        conn, cur = conn_mock
        cur.fetchone.return_value = _doc(archivo_s3_key="documentos/viejo.pdf")
        cur.execute.side_effect = [None, RuntimeError("BD caida")]
        resp = handler.actualizar_archivo(_event("Jefe_Maquinas", ID, body={"archivo_s3_key": "pendientes/nuevo.pdf"}), None)
        assert resp["statusCode"] == 500
        conn.rollback.assert_called_once()
        assert s3_valido == ["documentos/nuevo.pdf"]


# ---------------------------------------------------------------------------
class TestMarcarAtendido:
    def test_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.marcar_atendido(_event("Jefe_Maquinas", ID), None)["statusCode"] == 404

    def test_estado_invalido(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(estado="Archivado")
        assert handler.marcar_atendido(_event("Jefe_Maquinas", ID), None)["statusCode"] == 409

    def test_sin_permiso(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(seccion="Sanidad")
        assert handler.marcar_atendido(_event("Jefe_Maquinas", ID), None)["statusCode"] == 403

    @pytest.mark.parametrize("estado", ["Pendiente", "En proceso"])
    def test_exito_desde_pendiente_o_en_proceso(self, conn_mock, estado):
        conn, cur = conn_mock
        cur.fetchone.return_value = _doc(estado=estado)
        assert handler.marcar_atendido(_event("Jefe_Maquinas", ID), None)["statusCode"] == 200
        conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
class TestAsignarPrioridad:
    def test_prioridad_invalida(self, conn_mock):
        assert handler.asignar_prioridad(_event("Jefatura", ID, body={"prioridad": "Urgente"}), None)["statusCode"] == 400

    def test_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.asignar_prioridad(_event("Jefatura", ID, body={"prioridad": "Alta"}), None)["statusCode"] == 404

    def test_sin_permiso(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(seccion="Sanidad")
        assert handler.asignar_prioridad(_event("Jefe_Maquinas", ID, body={"prioridad": "Alta"}), None)["statusCode"] == 403

    @pytest.mark.parametrize("estado", ["En proceso", "Atendido", "Archivado"])
    def test_prioridad_congelada_fuera_de_pendiente(self, conn_mock, estado):
        """RN-0023 / CU-006: solo se asigna en Pendiente; el resto responde 409 y no toca la BD."""
        conn, cur = conn_mock
        cur.fetchone.return_value = _doc(estado=estado)
        resp = handler.asignar_prioridad(_event("Jefe_Maquinas", ID, body={"prioridad": "Baja"}), None)
        assert resp["statusCode"] == 409
        assert not [c for c in cur.execute.call_args_list if "UPDATE" in c.args[0]]
        conn.commit.assert_not_called()

    def test_sin_campos(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(estado="Pendiente")
        assert handler.asignar_prioridad(_event("Jefatura", ID, body={}), None)["statusCode"] == 400

    def test_fecha_mal_formateada(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(estado="Pendiente")
        assert handler.asignar_prioridad(_event("Jefatura", ID, body={"fecha_limite": "31/12/2026"}), None)["statusCode"] == 400

    def test_exito_marca_prioridad_manual(self, conn_mock):
        conn, cur = conn_mock
        cur.fetchone.return_value = _doc(estado="Pendiente")
        body = {"prioridad": "Alta", "fecha_limite": "2026-12-31"}
        assert handler.asignar_prioridad(_event("Jefatura", ID, body=body), None)["statusCode"] == 200
        update = [c for c in cur.execute.call_args_list if "UPDATE documentos" in c.args[0]][0]
        assert "prioridad_manual = true" in update.args[0]
        conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
ENVIO = {"medio": "Correo electrónico", "destinatario": "Municipalidad de Lima", "fecha_envio": "2026-10-08", "hora_envio": "14:30"}


class TestEnvioExterno:
    def test_exige_medio_y_destinatario(self, conn_mock):
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID, body={"medio": "Correo"}), None)["statusCode"] == 400
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID, body={"destinatario": "X"}), None)["statusCode"] == 400

    def test_fecha_u_hora_mal_formateadas(self, conn_mock):
        malo_fecha = {**ENVIO, "fecha_envio": "08/10/2026"}
        malo_hora = {**ENVIO, "hora_envio": "tarde"}
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID, body=malo_fecha), None)["statusCode"] == 400
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID, body=malo_hora), None)["statusCode"] == 400

    def test_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID, body=ENVIO), None)["statusCode"] == 404

    def test_ya_archivado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(estado="Archivado")
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID, body=ENVIO), None)["statusCode"] == 409

    def test_sin_permiso(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(seccion="Sanidad")
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID, body=ENVIO), None)["statusCode"] == 403

    def test_sin_descarga_previa_se_rechaza(self, conn_mock):
        """RN-0027 / CU-010: no se puede registrar el envío si el documento nunca se descargó."""
        conn, cur = conn_mock
        cur.fetchone.side_effect = [_doc(), None]  # documento, y ninguna descarga en el historial
        resp = handler.registrar_envio_externo(_event("Jefe_Maquinas", ID, body=ENVIO), None)
        assert resp["statusCode"] == 409
        assert "descargar" in json.loads(resp["body"])["error"].lower()
        assert not [c for c in cur.execute.call_args_list if "UPDATE documentos" in c.args[0]]
        conn.commit.assert_not_called()

    def test_la_descarga_se_busca_por_documento_y_usuario(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.side_effect = [_doc(), None]
        handler.registrar_envio_externo(_event("Jefe_Maquinas", ID, body=ENVIO), None)
        sql, params = [c for c in cur.execute.call_args_list if "accion = 'descarga'" in c.args[0]][0].args
        assert params == ("doc-1", "u-1")

    def test_exito_cierra_como_atendido_y_guarda_los_datos_del_envio(self, conn_mock):
        conn, cur = conn_mock
        cur.fetchone.side_effect = [_doc(), {"?column?": 1}]
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID, body=ENVIO), None)["statusCode"] == 200
        update = [c for c in cur.execute.call_args_list if "UPDATE documentos" in c.args[0]][0]
        assert "'Atendido'" in update.args[0]
        historial = [c for c in cur.execute.call_args_list if "INSERT INTO historial_acciones" in c.args[0]][0]
        accion, detalle = historial.args[1][4], historial.args[1][5]
        assert accion == "envio_externo"
        for dato in ("Correo electrónico", "Municipalidad de Lima", "08/10/2026", "14:30"):
            assert dato in detalle
        conn.commit.assert_called_once()

    def test_sin_fecha_ni_hora_usa_el_momento_actual_de_lima(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.side_effect = [_doc(), {"?column?": 1}]
        body = {"medio": "Mensajería", "destinatario": "INDECI"}
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID, body=body), None)["statusCode"] == 200
        detalle = [c for c in cur.execute.call_args_list if "INSERT INTO historial_acciones" in c.args[0]][0].args[1][5]
        assert "INDECI" in detalle
        assert "enviado el" in detalle.lower()

    def test_acepta_los_nombres_cortos_fecha_y_hora(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.side_effect = [_doc(), {"?column?": 1}]
        body = {"medio": "Correo", "destinatario": "X", "fecha": "2026-01-02", "hora": "09:05:00"}
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID, body=body), None)["statusCode"] == 200
        detalle = [c for c in cur.execute.call_args_list if "INSERT INTO historial_acciones" in c.args[0]][0].args[1][5]
        assert "02/01/2026" in detalle
        assert "09:05" in detalle

    def test_error_de_bd_hace_rollback(self, conn_mock):
        conn, cur = conn_mock
        cur.fetchone.side_effect = [_doc(), {"?column?": 1}]
        cur.execute.side_effect = [None, None, None, RuntimeError("BD caida")]
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID, body=ENVIO), None)["statusCode"] == 500
        conn.rollback.assert_called_once()


# ---------------------------------------------------------------------------
class TestDescargar:
    def test_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.descargar_documento(_event("Jefatura", ID), None)["statusCode"] == 404

    def test_sin_acceso(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.side_effect = [{"seccion_responsable": "Sanidad", "archivo_s3_key": "k"}, None]
        assert handler.descargar_documento(_event("Jefe_Maquinas", ID), None)["statusCode"] == 403

    def test_devuelve_url_y_registra_la_descarga(self, conn_mock, monkeypatch):
        conn, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Maquinas", "archivo_s3_key": "k"}
        monkeypatch.setattr(handler.s3util, "generar_url_descarga", lambda key: f"https://s3/{key}")
        resp = handler.descargar_documento(_event("Jefe_Maquinas", ID), None)
        assert json.loads(resp["body"]) == {"downloadUrl": "https://s3/k"}
        historial = [c for c in cur.execute.call_args_list if "INSERT INTO historial_acciones" in c.args[0]]
        assert len(historial) == 1 and historial[0].args[1][4] == "descarga"
        conn.commit.assert_called_once()

    def test_la_seccion_de_origen_puede_descargar_en_solo_lectura(self, conn_mock, monkeypatch):
        _, cur = conn_mock
        cur.fetchone.side_effect = [{"seccion_responsable": "Sanidad", "archivo_s3_key": "k"}, {"?column?": 1}]
        monkeypatch.setattr(handler.s3util, "generar_url_descarga", lambda key: "https://s3/k")
        assert handler.descargar_documento(_event("Jefe_Maquinas", ID), None)["statusCode"] == 200


# ---------------------------------------------------------------------------
class TestErroresInesperados:
    def test_derivar_devuelve_500_y_hace_rollback(self, conn_mock):
        conn, cur = conn_mock
        cur.execute.side_effect = [None, RuntimeError("BD caida")]
        cur.fetchone.return_value = {"seccion_responsable": "Maquinas"}
        resp = handler.derivar_documento(_event("Jefatura", ID, body={"seccion_destino": "Sanidad"}), None)
        assert resp["statusCode"] == 500
        conn.rollback.assert_called_once()

    def test_derivar_a_la_misma_seccion(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Maquinas"}
        assert handler.derivar_documento(_event("Jefatura", ID, body={"seccion_destino": "Maquinas"}), None)["statusCode"] == 400

    def test_eliminar_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.eliminar_documento(_event("Jefe_Administracion", ID), None)["statusCode"] == 404
