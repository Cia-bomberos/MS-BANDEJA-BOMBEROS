"""
Fecha y hora de referencia del módulo: siempre la de Lima (America/Lima).

Lambda corre en UTC. Entre las 19:00 y las 24:00 de Lima, `date.today()` del
servidor ya devuelve el día siguiente, lo que descuadraba los días restantes
hasta la fecha límite (y, por tanto, la prioridad) y el año del código único
en la noche del 31 de diciembre. Todo cálculo de "hoy" pasa por aquí.
"""

from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    LIMA = ZoneInfo("America/Lima")
except Exception:  # sin base de datos de zonas horarias en el runtime
    LIMA = timezone(timedelta(hours=-5))  # Lima no tiene horario de verano


def ahora_lima() -> datetime:
    return datetime.now(LIMA)


def hoy_lima() -> date:
    return ahora_lima().date()
