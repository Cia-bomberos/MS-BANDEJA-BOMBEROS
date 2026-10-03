from datetime import date, timedelta

from modulo_documentos.prioridad import ALTA, BAJA, MEDIA, calcular_prioridad, esta_vencido, reclasificar

HOY = date(2026, 1, 1)


class TestCalcularPrioridad:
    def test_menos_de_10_dias_es_alta(self):
        assert calcular_prioridad(HOY + timedelta(days=9), HOY) == ALTA

    def test_exactamente_10_dias_es_media(self):
        assert calcular_prioridad(HOY + timedelta(days=10), HOY) == MEDIA

    def test_exactamente_30_dias_es_media(self):
        assert calcular_prioridad(HOY + timedelta(days=30), HOY) == MEDIA

    def test_mas_de_30_dias_es_baja(self):
        assert calcular_prioridad(HOY + timedelta(days=31), HOY) == BAJA

    def test_fecha_ya_vencida_es_alta(self):
        assert calcular_prioridad(HOY - timedelta(days=1), HOY) == ALTA


class TestReclasificar:
    def test_bajo_10_dias_siempre_pasa_a_alta_aunque_sea_manual(self):
        fecha = HOY + timedelta(days=5)
        assert reclasificar(BAJA, prioridad_manual=True, fecha_limite=fecha, hoy=HOY) == ALTA

    def test_manual_se_respeta_fuera_del_umbral_de_alta(self):
        fecha = HOY + timedelta(days=25)
        # Asignación manual "Baja" se respeta aunque el cálculo automático diría Media
        assert reclasificar(BAJA, prioridad_manual=True, fecha_limite=fecha, hoy=HOY) == BAJA

    def test_automatica_se_recalcula_normalmente(self):
        fecha = HOY + timedelta(days=25)
        assert reclasificar(BAJA, prioridad_manual=False, fecha_limite=fecha, hoy=HOY) == MEDIA


class TestEstaVencido:
    def test_fecha_pasada_esta_vencido(self):
        assert esta_vencido(HOY - timedelta(days=1), HOY) is True

    def test_fecha_hoy_no_esta_vencido(self):
        assert esta_vencido(HOY, HOY) is False

    def test_fecha_futura_no_esta_vencido(self):
        assert esta_vencido(HOY + timedelta(days=1), HOY) is False
