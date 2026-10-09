"""
Autorización por rol/sección (RN-0004, RN-0005, RF-0002), a partir de los
claims que inyecta el Authorizer de Cognito en el event de API Gateway.

Reutiliza el mismo User Pool (y por lo tanto los mismos grupos/roles) que
MS-SEGURIDAD-BOMBEROS: Jefatura, Jefe_Administracion, Jefe_ServicioGeneral,
Jefe_Maquinas, Jefe_Sanidad.
"""

SECCIONES_VALIDAS = {"Administracion", "ServicioGeneral", "Maquinas", "Sanidad"}

GRUPO_SECCION = {
    "Jefe_Administracion": "Administracion",
    "Jefe_ServicioGeneral": "ServicioGeneral",
    "Jefe_Maquinas": "Maquinas",
    "Jefe_Sanidad": "Sanidad",
}

JEFATURA = "Jefatura"
ADMINISTRACION = "Administracion"


def _claims(event) -> dict:
    return event.get("requestContext", {}).get("authorizer", {}).get("claims", {})


def grupo(event) -> str | None:
    grupos = _claims(event).get("cognito:groups", "")
    lista = grupos.split(",") if isinstance(grupos, str) else grupos
    return lista[0] if lista else None


def usuario_sub(event) -> str | None:
    return _claims(event).get("sub")


def usuario_nombre(event) -> str | None:
    return _claims(event).get("name")


def es_jefatura(event) -> bool:
    return grupo(event) == JEFATURA


def es_jefe_administracion(event) -> bool:
    return grupo(event) == "Jefe_Administracion"


def seccion_usuario(event) -> str | None:
    """Sección de la cuenta autenticada. None para Jefatura (no tiene una sola sección)."""
    return GRUPO_SECCION.get(grupo(event))


def puede_leer(event, seccion_doc: str) -> bool:
    """RN-0004, RN-0005: Jefatura y Jefe_Administracion leen todas las secciones."""
    g = grupo(event)
    if g in (JEFATURA, "Jefe_Administracion"):
        return True
    return seccion_usuario(event) == seccion_doc


def puede_escribir(event, seccion_doc: str) -> bool:
    """RF-0002: Jefatura escribe en cualquier sección; Jefe_Administracion solo en la suya."""
    g = grupo(event)
    if g == JEFATURA:
        return True
    return seccion_usuario(event) == seccion_doc
