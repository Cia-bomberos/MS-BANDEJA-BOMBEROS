from modulo_documentos.codigo import formatear_codigo


def test_formato_basico():
    assert formatear_codigo("nota informativa", 110, 2026) == "NOTA INFORMATIVA N° 110-2026/CGBVP/IVCDLC/B3"


def test_numero_se_rellena_con_ceros():
    assert formatear_codigo("oficio", 7, 2026) == "OFICIO N° 007-2026/CGBVP/IVCDLC/B3"


def test_numero_de_cuatro_digitos_no_se_trunca():
    assert formatear_codigo("oficio", 1234, 2026) == "OFICIO N° 1234-2026/CGBVP/IVCDLC/B3"
