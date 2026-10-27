"""Tests de modulo_documentos/configuracion.py (carpeta de Drive configurable). BD y Drive mockeados."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from modulo_documentos import configuracion, drive

ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz_-0123"
ENLACE = f"https://drive.google.com/drive/folders/{ID}?usp=sharing"
FILA = {
    "valor": {"folder_id": ID, "nombre": "SOFTWARE_BOMBEROS"},
    "actualizado_nombre": "Admin",
    "actualizado_en": datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc),
}


def _event(grupo="Jefe_Administracion", body=None):
    ev = {"requestContext": {"authorizer": {"claims": {"cognito:groups": grupo, "sub": "u-1", "name": "Admin"}}}}
    if body is not None:
        ev["body"] = body if isinstance(body, str) else json.dumps(body)
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
    monkeypatch.setattr(configuracion, "get_connection", lambda: conn)
    return conn, cursor


@pytest.fixture
def drive_ok(monkeypatch):
    monkeypatch.setattr(drive, "_token", lambda s3=None: "tok")
    monkeypatch.setattr(drive, "cuenta_respaldo", lambda token: "respaldo@gmail.com")
    monkeypatch.setattr(drive, "verificar_carpeta", lambda fid, token: {"id": fid, "nombre": "SOFTWARE_BOMBEROS"})


GRUPOS_SIN_PERMISO = ["Jefatura", "Jefe_Maquinas", "Jefe_Sanidad", "Jefe_ServicioGeneral", ""]


class TestPermisos:
    @pytest.mark.parametrize("grupo", GRUPOS_SIN_PERMISO)
    def test_get_solo_para_jefe_de_administracion(self, conn_mock, grupo):
        assert configuracion.obtener_config_drive(_event(grupo), None)["statusCode"] == 403

    @pytest.mark.parametrize("grupo", GRUPOS_SIN_PERMISO)
    def test_put_solo_para_jefe_de_administracion(self, conn_mock, drive_ok, grupo):
        r = configuracion.guardar_config_drive(_event(grupo, {"enlace": ENLACE}), None)
        assert r["statusCode"] == 403
        conn_mock[0].commit.assert_not_called()


class TestObtener:
    def test_devuelve_la_carpeta_configurada_y_la_cuenta_a_la_que_compartir(self, conn_mock, drive_ok):
        _, cur = conn_mock
        cur.fetchone.return_value = FILA
        r = configuracion.obtener_config_drive(_event(), None)
        body = json.loads(r["body"])
        assert r["statusCode"] == 200
        assert body["configurado"] is True
        assert body["origen"] == "configuracion"
        assert body["carpeta_id"] == ID
        assert body["enlace"] == f"https://drive.google.com/drive/folders/{ID}"
        assert body["cuenta_respaldo"] == "respaldo@gmail.com"
        assert body["actualizado_por"] == "Admin"

    def test_sin_configurar_cae_a_la_variable_de_entorno(self, conn_mock, drive_ok, monkeypatch):
        monkeypatch.setenv("DRIVE_FOLDER_ID", "de-entorno-123")
        conn_mock[1].fetchone.return_value = None
        body = json.loads(configuracion.obtener_config_drive(_event(), None)["body"])
        assert (body["configurado"], body["origen"], body["carpeta_id"]) == (True, "variable_de_entorno", "de-entorno-123")

    def test_sin_nada_configurado(self, conn_mock, drive_ok, monkeypatch):
        monkeypatch.delenv("DRIVE_FOLDER_ID", raising=False)
        conn_mock[1].fetchone.return_value = None
        body = json.loads(configuracion.obtener_config_drive(_event(), None)["body"])
        assert body["configurado"] is False
        assert body["carpeta_id"] is None
        assert body["enlace"] is None

    def test_sin_credenciales_de_google_igual_responde(self, conn_mock, monkeypatch):
        def _falla(s3=None):
            raise RuntimeError("NoSuchKey")

        monkeypatch.setattr(drive, "_token", _falla)
        conn_mock[1].fetchone.return_value = FILA
        r = configuracion.obtener_config_drive(_event(), None)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["cuenta_respaldo"] is None

    def test_cierra_la_conexion(self, conn_mock, drive_ok):
        conn_mock[1].fetchone.return_value = FILA
        configuracion.obtener_config_drive(_event(), None)
        conn_mock[0].close.assert_called_once()


class TestGuardar:
    def test_guarda_la_carpeta_verificada(self, conn_mock, drive_ok):
        conn, cur = conn_mock
        cur.fetchone.return_value = FILA
        r = configuracion.guardar_config_drive(_event(body={"enlace": ENLACE}), None)
        assert r["statusCode"] == 200
        insert = [c for c in cur.execute.call_args_list if "INSERT INTO configuracion" in c.args[0]][0]
        valor, sub, nombre = insert.args[1]
        assert json.loads(valor) == {"folder_id": ID, "nombre": "SOFTWARE_BOMBEROS"}
        assert (sub, nombre) == ("u-1", "Admin")
        conn.commit.assert_called_once()
        assert json.loads(r["body"])["cuenta_respaldo"] == "respaldo@gmail.com"

    def test_acepta_la_clave_link(self, conn_mock, drive_ok):
        conn_mock[1].fetchone.return_value = FILA
        assert configuracion.guardar_config_drive(_event(body={"link": ENLACE}), None)["statusCode"] == 200

    @pytest.mark.parametrize("body", [{}, {"enlace": ""}, {"enlace": "hola"}, {"enlace": "https://evil.com/x"}])
    def test_enlace_invalido_400_sin_tocar_nada(self, conn_mock, drive_ok, body):
        r = configuracion.guardar_config_drive(_event(body=body), None)
        assert r["statusCode"] == 400
        conn_mock[0].commit.assert_not_called()

    def test_cuerpo_no_json_400(self, conn_mock, drive_ok):
        assert configuracion.guardar_config_drive(_event(body="{no es json"), None)["statusCode"] == 400

    def test_carpeta_sin_permiso_422_indica_a_quien_compartir(self, conn_mock, drive_ok, monkeypatch):
        def _sin_permiso(fid, token):
            raise drive.ErrorCarpeta("La cuenta de respaldo solo puede ver la carpeta: debe compartirla como Editor.")

        monkeypatch.setattr(drive, "verificar_carpeta", _sin_permiso)
        r = configuracion.guardar_config_drive(_event(body={"enlace": ENLACE}), None)
        assert r["statusCode"] == 422
        assert "respaldo@gmail.com" in json.loads(r["body"])["error"]
        conn_mock[0].commit.assert_not_called()

    def test_422_sin_cuenta_conocida(self, conn_mock, drive_ok, monkeypatch):
        monkeypatch.setattr(drive, "cuenta_respaldo", lambda token: None)
        monkeypatch.setattr(drive, "verificar_carpeta", lambda *a: (_ for _ in ()).throw(drive.ErrorCarpeta("no existe")))
        r = configuracion.guardar_config_drive(_event(body={"enlace": ENLACE}), None)
        assert r["statusCode"] == 422
        assert json.loads(r["body"])["error"] == "no existe"

    def test_credenciales_vencidas_502(self, conn_mock, monkeypatch):
        def _falla(s3=None):
            raise RuntimeError("invalid_grant")

        monkeypatch.setattr(drive, "_token", _falla)
        r = configuracion.guardar_config_drive(_event(body={"enlace": ENLACE}), None)
        assert r["statusCode"] == 502
        conn_mock[0].commit.assert_not_called()

    def test_google_caido_502(self, conn_mock, drive_ok, monkeypatch):
        monkeypatch.setattr(drive, "verificar_carpeta", lambda *a: (_ for _ in ()).throw(RuntimeError("HTTP 500")))
        assert configuracion.guardar_config_drive(_event(body={"enlace": ENLACE}), None)["statusCode"] == 502

    def test_error_de_bd_500_y_rollback(self, conn_mock, drive_ok):
        conn, cur = conn_mock
        cur.execute.side_effect = RuntimeError("BD caida")
        r = configuracion.guardar_config_drive(_event(body={"enlace": ENLACE}), None)
        assert r["statusCode"] == 500
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()
