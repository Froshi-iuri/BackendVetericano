"""
Constantes maestras del módulo de inventario de dos niveles:
Bodega General CBA + Kit por Veterinario.
Proyecto VETERICANO - Centro de Bienestar Animal (CBA) Popayán.
"""

# ----------------------------------------------------------------------------
# 1. ROLES DE USUARIO (R10)
# ----------------------------------------------------------------------------
# Roles autorizados para gestionar inventario (asignar, retirar, resolver solicitudes)
# Actualmente Rol 1: Administrador. A futuro: Rol 2: Jurídico.
INVENTARIO_GESTOR_ROLES = [1]
ROL_VETERINARIO = 3

# Roles autorizados para lectura en viewsets antiguos de inventario (Administrador, Juridico, Veterinario)
INVENTARIO_LECTURA_LEGACY_ROLES = [1, 2, 3]

# Roles autorizados para PATCH limitado de cantidad_actual y estado en inventarios (acta web)
INVENTARIO_AJUSTE_STOCK_LEGACY_ROLES = [2, 3]

# ----------------------------------------------------------------------------
# 2. UMBRALES DE ALERTA DE STOCK Y VENCIMIENTO (R11)
# ----------------------------------------------------------------------------
STOCK_UMBRAL_CRITICO = 5       # Alerta roja: menos de 5 unidades
STOCK_UMBRAL_BAJO = 10         # Alerta amarilla: menos de 10 unidades
VENTANA_DIAS_VENCIMIENTO = 30  # Próximo a vencer: dentro de los próximos 30 días

# ----------------------------------------------------------------------------
# 3. VALORES CANÓNICOS DEL ESQUEMA SQL v2 (plan/supabase_inventario_v2.sql)
# ----------------------------------------------------------------------------
# Tipos de movimiento permitidos en inventario_kardex (CHECK chk_kardex_tipo)
TIPO_KARDEX_ASIGNACION_KIT = 'ASIGNACION_KIT'
TIPO_KARDEX_CONSUMO_HISTORIA = 'CONSUMO_HISTORIA'
TIPO_KARDEX_CONSUMO_ACTA = 'CONSUMO_ACTA'
TIPO_KARDEX_DEVOLUCION_APROBADA = 'DEVOLUCION_APROBADA'
TIPO_KARDEX_RETIRO_GESTOR = 'RETIRO_GESTOR'
TIPO_KARDEX_BAJA_VENCIMIENTO = 'BAJA_VENCIMIENTO'
TIPO_KARDEX_AJUSTE_REVERSION = 'AJUSTE_REVERSION'
TIPO_KARDEX_ENTRADA_COMPRA = 'ENTRADA_COMPRA'

KARDEX_TIPOS_PERMITIDOS = [
    TIPO_KARDEX_ASIGNACION_KIT,
    TIPO_KARDEX_CONSUMO_HISTORIA,
    TIPO_KARDEX_CONSUMO_ACTA,
    TIPO_KARDEX_DEVOLUCION_APROBADA,
    TIPO_KARDEX_RETIRO_GESTOR,
    TIPO_KARDEX_BAJA_VENCIMIENTO,
    TIPO_KARDEX_AJUSTE_REVERSION,
    TIPO_KARDEX_ENTRADA_COMPRA,
]

# Ubicaciones del Kardex (CHECK chk_kardex_ubicacion)
UBICACION_KARDEX_GENERAL = 'GENERAL'
UBICACION_KARDEX_KIT = 'KIT_VETERINARIO'

KARDEX_UBICACIONES_PERMITIDAS = [
    UBICACION_KARDEX_GENERAL,
    UBICACION_KARDEX_KIT,
]

# Solicitudes de Kit (CHECK chk_solicitud_tipo y chk_solicitud_estado)
SOLICITUD_TIPO_REPOSICION = 'REPOSICION'
SOLICITUD_TIPO_DEVOLUCION = 'DEVOLUCION'

SOLICITUD_TIPOS_PERMITIDOS = [
    SOLICITUD_TIPO_REPOSICION,
    SOLICITUD_TIPO_DEVOLUCION,
]

SOLICITUD_ESTADO_PENDIENTE = 'PENDIENTE'
SOLICITUD_ESTADO_APROBADA = 'APROBADA'
SOLICITUD_ESTADO_PARCIAL = 'PARCIAL'
SOLICITUD_ESTADO_RECHAZADA = 'RECHAZADA'
SOLICITUD_ESTADO_CANCELADA = 'CANCELADA'

SOLICITUD_ESTADOS_PERMITIDOS = [
    SOLICITUD_ESTADO_PENDIENTE,
    SOLICITUD_ESTADO_APROBADA,
    SOLICITUD_ESTADO_PARCIAL,
    SOLICITUD_ESTADO_RECHAZADA,
    SOLICITUD_ESTADO_CANCELADA,
]

# Consumos de Registro (CHECK chk_consumo_estado y chk_consumo_origen)
CONSUMO_ESTADO_APLICADO = 'APLICADO'
CONSUMO_ESTADO_RECHAZADO_CONFLICTO = 'RECHAZADO_CONFLICTO'
CONSUMO_ESTADO_REVERTIDO = 'REVERTIDO'

CONSUMO_ESTADOS_PERMITIDOS = [
    CONSUMO_ESTADO_APLICADO,
    CONSUMO_ESTADO_RECHAZADO_CONFLICTO,
    CONSUMO_ESTADO_REVERTIDO,
]

CONSUMO_ORIGEN_HISTORIA = 'HISTORIA_CLINICA'
CONSUMO_ORIGEN_ACTA = 'ACTA_VISITA'
CONSUMO_ORIGEN_DIRECTO = 'DIRECTO'

CONSUMO_ORIGENES_PERMITIDOS = [
    CONSUMO_ORIGEN_HISTORIA,
    CONSUMO_ORIGEN_ACTA,
    CONSUMO_ORIGEN_DIRECTO,
]
