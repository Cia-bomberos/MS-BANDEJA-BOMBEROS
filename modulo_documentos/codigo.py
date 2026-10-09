"""Generación del código único de documento (RN-0014)."""

FORMATO_FIJO = "CGBVP/IVCDLC/B3"


def formatear_codigo(tipo: str, numero: int, anio: int) -> str:
    """ej. 'NOTA INFORMATIVA N° 110-2026/CGBVP/IVCDLC/B3'"""
    return f"{tipo.upper()} N° {numero:03d}-{anio}/{FORMATO_FIJO}"
