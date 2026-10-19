"""
Utilidades y helpers para la construccion de payloads y aserciones del modulo.
"""

from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken
from inventario.models import InventarioKardex, InventarioConsumosRegistro


def obtener_headers_jwt(usuario):
    """
    Genera un token JWT de acceso para el usuario especificado
    y retorna el encabezado HTTP de autorizacion para el APIClient de Django REST Framework.
    """
    refresh = RefreshToken.for_user(usuario)
    return {"HTTP_AUTHORIZATION": f"Bearer {str(refresh.access_token)}"}


def payload_expediente_clinico(id_animal=None, animal_nuevo=None, consumos=None, offline=False, motivo_consulta="Control medico y evaluacion", hoja2=None):
    """
    Construye el payload exacto de POST /api/clinica/expediente-completo/.
    Garantiza que motivoConsulta, medicamentos_usados y sincronizacion_offline vivan en la raiz.
    """
    if hoja2 is None:
        hoja2 = {
            "diagnosticoFinal": "Paciente evaluado y medicado",
            "tratamientos": []
        }

    payload = {
        "motivoConsulta": motivo_consulta,
        "hoja2": hoja2,
        "medicamentos_usados": consumos if consumos is not None else [],
        "sincronizacion_offline": offline
    }

    if id_animal is not None:
        payload["id_animal"] = id_animal
    elif animal_nuevo is not None:
        payload["animal"] = animal_nuevo

    return payload


def payload_acta_visita(id_peticion, id_veterinario, consumos=None, offline=False, observacion="Atención de visita de campo con administración de medicamentos"):
    """
    Construye el payload exacto de POST /api/peticiones/seguimiento/crear/.
    Incluye id_veterinario obligatorio en el body y fecha_atencion relativa ISO.
    """
    hoy_iso = timezone.now().date().isoformat()
    return {
        "id_peticion": id_peticion,
        "id_veterinario": id_veterinario,
        "observacion": observacion,
        "fecha_atencion": hoy_iso,
        "medicamentos_usados": consumos if consumos is not None else [],
        "sincronizacion_offline": offline
    }


def assert_kardex_consistente(test_case, client_uuid, tipo_movimiento, cantidad_esperada=None):
    """
    Verifica que existan registros de kardex consistentes para el client_uuid y tipo indicados.
    Si se especifica cantidad_esperada, comprueba que la suma de cantidades coincida.
    """
    qs = InventarioKardex.objects.filter(client_uuid=client_uuid, tipo_movimiento=tipo_movimiento)
    test_case.assertTrue(qs.exists(), f"No se encontraron filas de kardex para client_uuid={client_uuid} y tipo={tipo_movimiento}")

    if cantidad_esperada is not None:
        total = sum(r.cantidad for r in qs)
        test_case.assertEqual(total, cantidad_esperada, f"La cantidad total en kardex ({total}) no coincide con la esperada ({cantidad_esperada})")


def assert_consumo_registro(test_case, client_uuid, estado_esperado, origen_tipo=None):
    """
    Verifica que el registro de auditoria en InventarioConsumosRegistro exista con el estado y origen esperados.
    """
    reg = InventarioConsumosRegistro.objects.filter(client_uuid=client_uuid).first()
    test_case.assertIsNotNone(reg, f"No se encontro InventarioConsumosRegistro para client_uuid={client_uuid}")
    test_case.assertEqual(reg.estado, estado_esperado, f"Estado esperado {estado_esperado}, obtenido {reg.estado}")

    if origen_tipo is not None:
        test_case.assertEqual(reg.origen_tipo, origen_tipo, f"Origen esperado {origen_tipo}, obtenido {reg.origen_tipo}")

    return reg
