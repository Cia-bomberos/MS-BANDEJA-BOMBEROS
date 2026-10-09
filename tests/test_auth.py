from modulo_documentos import auth


def _event(grupo=""):
    return {"requestContext": {"authorizer": {"claims": {"cognito:groups": grupo, "sub": "u-1", "name": "Tester"}}}}


class TestGrupoYSeccion:
    def test_seccion_usuario_mapea_grupo_a_seccion(self):
        assert auth.seccion_usuario(_event("Jefe_Maquinas")) == "Maquinas"

    def test_jefatura_no_tiene_seccion_propia(self):
        assert auth.seccion_usuario(_event("Jefatura")) is None

    def test_es_jefatura(self):
        assert auth.es_jefatura(_event("Jefatura")) is True
        assert auth.es_jefatura(_event("Jefe_Sanidad")) is False

    def test_es_jefe_administracion(self):
        assert auth.es_jefe_administracion(_event("Jefe_Administracion")) is True
        assert auth.es_jefe_administracion(_event("Jefe_Sanidad")) is False


class TestPuedeLeer:
    def test_jefatura_lee_cualquier_seccion(self):
        assert auth.puede_leer(_event("Jefatura"), "Maquinas") is True

    def test_jefe_administracion_lee_cualquier_seccion(self):
        assert auth.puede_leer(_event("Jefe_Administracion"), "Sanidad") is True

    def test_jefe_de_seccion_solo_lee_la_propia(self):
        assert auth.puede_leer(_event("Jefe_Maquinas"), "Maquinas") is True
        assert auth.puede_leer(_event("Jefe_Maquinas"), "Sanidad") is False


class TestPuedeEscribir:
    def test_jefatura_escribe_en_cualquier_seccion(self):
        assert auth.puede_escribir(_event("Jefatura"), "ServicioGeneral") is True

    def test_jefe_administracion_solo_escribe_en_administracion(self):
        assert auth.puede_escribir(_event("Jefe_Administracion"), "Administracion") is True
        assert auth.puede_escribir(_event("Jefe_Administracion"), "Sanidad") is False

    def test_jefe_de_seccion_solo_escribe_en_la_propia(self):
        assert auth.puede_escribir(_event("Jefe_Sanidad"), "Sanidad") is True
        assert auth.puede_escribir(_event("Jefe_Sanidad"), "Maquinas") is False
