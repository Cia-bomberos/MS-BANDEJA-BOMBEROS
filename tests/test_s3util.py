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
    a, b = s3util.generar_key(), s3util.generar_key()
    assert a.startswith("pendientes/")
    assert a.endswith(".pdf")
    assert a != b


def test_url_de_subida_exige_content_type_pdf(s3_mock):
    s3_mock.generate_presigned_url.return_value = "https://s3/firmada"
    r = s3util.generar_url_subida()
    assert r["uploadUrl"] == "https://s3/firmada"
    assert r["expiraEn"] == 300
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


def test_url_de_subida_firma_el_tamano_si_se_informa(s3_mock):
    s3_mock.generate_presigned_url.return_value = "https://s3/firmada"
    s3util.generar_url_subida(tamano_bytes=2048)
    assert s3_mock.generate_presigned_url.call_args.kwargs["Params"]["ContentLength"] == 2048


def test_url_de_subida_sin_tamano_no_lo_firma(s3_mock):
    s3_mock.generate_presigned_url.return_value = "u"
    s3util.generar_url_subida()
    assert "ContentLength" not in s3_mock.generate_presigned_url.call_args.kwargs["Params"]


def test_validar_pdf_rechaza_archivos_vacios_sin_pedir_el_contenido(s3_mock):
    s3_mock.head_object.return_value = {"ContentLength": 0}
    assert s3util.validar_pdf("k") is False
    s3_mock.get_object.assert_not_called()


def test_validar_pdf_rechaza_si_falla_la_lectura(s3_mock):
    s3_mock.head_object.return_value = {"ContentLength": 100}
    s3_mock.get_object.side_effect = RuntimeError("InvalidRange")
    assert s3util.validar_pdf("k") is False


def test_eliminar_borra_el_objeto(s3_mock):
    s3util.eliminar("pendientes/a.pdf")
    s3_mock.delete_object.assert_called_once_with(
        Bucket="bomberos-documentos-test", Key="pendientes/a.pdf", ExpectedBucketOwner="123456789012"
    )


def test_eliminar_nunca_lanza(s3_mock):
    s3_mock.delete_object.side_effect = RuntimeError("AccessDenied")
    s3util.eliminar("pendientes/a.pdf")  # no debe propagar


class TestPromoverPdf:
    def test_mueve_el_pdf_valido_a_documentos(self, s3_mock):
        s3_mock.head_object.return_value = {"ContentLength": 1000}
        s3_mock.get_object.return_value = {"Body": io.BytesIO(b"%PDF-")}
        nueva = s3util.promover_pdf("pendientes/a.pdf")
        assert nueva.startswith("documentos/")
        assert nueva.endswith(".pdf")
        copia = s3_mock.copy_object.call_args.kwargs
        assert copia["Key"] == nueva
        assert copia["CopySource"]["Key"] == "pendientes/a.pdf"
        s3_mock.delete_object.assert_called_once_with(
            Bucket="bomberos-documentos-test", Key="pendientes/a.pdf", ExpectedBucketOwner="123456789012"
        )

    def test_pdf_invalido_se_elimina_de_s3(self, s3_mock):
        s3_mock.head_object.return_value = {"ContentLength": 10}
        s3_mock.get_object.return_value = {"Body": io.BytesIO(b"MZ...")}
        assert s3util.promover_pdf("pendientes/a.exe.pdf") is None
        s3_mock.delete_object.assert_called_once_with(
            Bucket="bomberos-documentos-test", Key="pendientes/a.exe.pdf", ExpectedBucketOwner="123456789012"
        )
        s3_mock.copy_object.assert_not_called()

    def test_archivo_de_mas_de_20mb_se_elimina(self, s3_mock):
        s3_mock.head_object.return_value = {"ContentLength": s3util.MAX_BYTES + 1}
        assert s3util.promover_pdf("pendientes/grande.pdf") is None
        s3_mock.delete_object.assert_called_once()

    @pytest.mark.parametrize("key", ["config/google-oauth.json", "documentos/ajeno.pdf", "", None, 123])
    def test_keys_fuera_de_pendientes_se_rechazan_y_nunca_se_borran(self, s3_mock, key):
        assert s3util.promover_pdf(key) is None
        s3_mock.delete_object.assert_not_called()
        s3_mock.copy_object.assert_not_called()


def test_todas_las_llamadas_a_s3_envian_el_dueno_del_bucket(s3_mock):
    """ExpectedBucketOwner en head/get/copy/delete: S3 rechaza el bucket si no es de nuestra cuenta."""
    s3_mock.head_object.return_value = {"ContentLength": 1000}
    s3_mock.get_object.return_value = {"Body": io.BytesIO(b"%PDF-")}
    s3util.promover_pdf("pendientes/a.pdf")
    for llamada in (s3_mock.head_object, s3_mock.get_object, s3_mock.copy_object, s3_mock.delete_object):
        assert llamada.call_args.kwargs["ExpectedBucketOwner"] == "123456789012"
