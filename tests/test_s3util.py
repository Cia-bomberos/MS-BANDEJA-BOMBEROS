"""Tests de modulo_documentos/s3util.py con boto3 mockeado."""

import io
from unittest.mock import MagicMock

import pytest

from modulo_documentos import s3util


@pytest.fixture
def s3_mock(monkeypatch):
    m = MagicMock()
    m.exceptions.ClientError = RuntimeError
    monkeypatch.setattr(s3util, "s3", m)
    return m


def test_generar_key_siempre_termina_en_pdf_y_es_unica():
    a, b = s3util.generar_key("../../evil.exe"), s3util.generar_key("x.pdf")
    assert a.startswith("documentos/") and a.endswith(".pdf") and a != b


def test_url_de_subida_exige_content_type_pdf(s3_mock):
    s3_mock.generate_presigned_url.return_value = "https://s3/firmada"
    r = s3util.generar_url_subida("a.pdf")
    assert r["uploadUrl"] == "https://s3/firmada" and r["expiraEn"] == 300
    assert s3_mock.generate_presigned_url.call_args.kwargs["Params"]["ContentType"] == "application/pdf"


def test_url_de_descarga(s3_mock):
    s3_mock.generate_presigned_url.return_value = "https://s3/get"
    assert s3util.generar_url_descarga("k") == "https://s3/get"


def test_validar_pdf_ok(s3_mock):
    s3_mock.head_object.return_value = {"ContentLength": 1000}
    s3_mock.get_object.return_value = {"Body": io.BytesIO(b"%PDF-")}
    assert s3util.validar_pdf("k") is True


def test_validar_pdf_rechaza_si_no_existe(s3_mock):
    s3_mock.head_object.side_effect = RuntimeError("404")
    assert s3util.validar_pdf("k") is False


def test_validar_pdf_rechaza_si_pesa_mas_de_20mb(s3_mock):
    s3_mock.head_object.return_value = {"ContentLength": s3util.MAX_BYTES + 1}
    assert s3util.validar_pdf("k") is False


def test_validar_pdf_rechaza_si_no_tiene_firma_pdf(s3_mock):
    s3_mock.head_object.return_value = {"ContentLength": 10}
    s3_mock.get_object.return_value = {"Body": io.BytesIO(b"MZ...")}
    assert s3util.validar_pdf("k") is False
