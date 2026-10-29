"""Tests de modulo_documentos/tiempo.py: el 'hoy' del sistema es el de Lima."""

from datetime import datetime, timezone, timedelta, date

from modulo_documentos import tiempo


def test_lima_va_5_horas_detras_de_utc():
    utc = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)
    lima = utc.astimezone(tiempo.LIMA)
    assert (lima.day, lima.hour) == (8, 22)


def test_hoy_lima_no_adelanta_el_dia_de_noche(monkeypatch):
    class Falso(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 12, 31, 23, 30, tzinfo=tiempo.LIMA).astimezone(tz) if tz else None

    monkeypatch.setattr(tiempo, "datetime", Falso)
    assert tiempo.hoy_lima().isoformat() == "2026-12-31"  # en UTC ya sería 2027-01-01
    assert tiempo.ahora_lima().year == 2026

def test_ahora_lima_devuelve_datetime_con_zona_lima():
    ahora = tiempo.ahora_lima()
    assert isinstance(ahora, datetime)
    # Lima está en UTC-5 sin horario de verano
    assert ahora.utcoffset() == timedelta(hours=-5)


def test_hoy_lima_devuelve_date():
    assert isinstance(tiempo.hoy_lima(), date)


def test_fallback_sin_zoneinfo(monkeypatch):
    """Cubre el `except` cuando el runtime no tiene tzdata."""
    import importlib
    import modulo_documentos.tiempo as mod
    importlib.reload(mod)
    # Forzamos el bloque except simulando que ZoneInfo falla
    # (reimportamos con un sys.modules manipulado)
    import sys
    real_zoneinfo = sys.modules.get("zoneinfo")
    try:
        sys.modules["zoneinfo"] = None
        importlib.reload(mod)
        # con zoneinfo=None, el import lanza y se usa el fallback
        assert mod.LIMA == timezone(timedelta(hours=-5))
    finally:
        if real_zoneinfo is not None:
            sys.modules["zoneinfo"] = real_zoneinfo
        else:
            sys.modules.pop("zoneinfo", None)
        importlib.reload(mod)