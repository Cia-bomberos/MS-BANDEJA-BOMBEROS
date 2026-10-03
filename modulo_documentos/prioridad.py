"""
Cálculo de prioridad (RN-0017, RN-0018, RF-0005).

Funciones puras, sin dependencias de AWS ni de la BD, para que sean
triviales de testear.
"""

from datetime import date

ALTA = "Alta"
MEDIA = "Media"
BAJA = "Baja"

UMBRAL_ALTA_DIAS = 10   # < 10 días restantes -> Alta
UMBRAL_MEDIA_DIAS = 30  # 10-30 días -> Media; > 30 -> Baja


def dias_restantes(fecha_limite: date, hoy: date | None = None) -> int:
    hoy = hoy or date.today()
    return (fecha_limite - hoy).days


def calcular_prioridad(fecha_limite: date, hoy: date | None = None) -> str:
    restantes = dias_restantes(fecha_limite, hoy)
    if restantes < UMBRAL_ALTA_DIAS:
        return ALTA
    if restantes <= UMBRAL_MEDIA_DIAS:
        return MEDIA
    return BAJA


def reclasificar(prioridad_actual: str, prioridad_manual: bool, fecha_limite: date, hoy: date | None = None) -> str:
    """Reevalúa la prioridad de un documento en estado Pendiente.

    RN-0018: una asignación manual se respeta mientras el plazo restante
    siga dentro de su rango, pero si baja de 10 días el sistema siempre
    reclasifica a Alta, incluso sobre una asignación manual.
    """
    restantes = dias_restantes(fecha_limite, hoy)
    if restantes < UMBRAL_ALTA_DIAS:
        return ALTA
    if prioridad_manual:
        return prioridad_actual
    return calcular_prioridad(fecha_limite, hoy)


def esta_vencido(fecha_limite: date, hoy: date | None = None) -> bool:
    """RN-0019: resaltar documentos que superaron su fecha límite (sin cambiar su estado)."""
    return dias_restantes(fecha_limite, hoy) < 0
