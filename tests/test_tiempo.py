"""Tests de modulo_documentos/tiempo.py: el 'hoy' del sistema es el de Lima."""

from datetime import datetime, timezone

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
