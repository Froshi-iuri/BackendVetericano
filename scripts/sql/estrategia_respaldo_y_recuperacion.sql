-- =============================================================================
-- VETERICANO - Estrategia de respaldo, verificacion y recuperacion (SOLO REFERENCIA)
-- NO ejecutar este archivo completo como script ciego: contiene bloques de referencia
-- para: (1) respaldo previo en schema separado, (2) verificacion pre/post y
-- (3) protocolo de contencion / recuperacion sin operaciones destructivas sobre datos previos.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. RESPALDO PREVIO EN SCHEMA SEPARADO (patron consistente con respaldo_20261106)
--    Aditivo y no bloqueante sobre escrituras prolongadas si se ejecuta antes de la ventana.
--    Ademas realizar snapshot/backup logico externo con pg_dump previo al despliegue.
-- -----------------------------------------------------------------------------
/*
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

CREATE SCHEMA IF NOT EXISTS respaldo_20261112_pre_mig;

CREATE TABLE IF NOT EXISTS respaldo_20261112_pre_mig.django_migrations AS
    TABLE public.django_migrations;

CREATE TABLE IF NOT EXISTS respaldo_20261112_pre_mig.peticiones AS
    TABLE public.peticiones;

CREATE TABLE IF NOT EXISTS respaldo_20261112_pre_mig.notificaciones_peticiones AS
    TABLE public.notificaciones_peticiones;

CREATE TABLE IF NOT EXISTS respaldo_20261112_pre_mig.historial_peticiones AS
    TABLE public.historial_peticiones;

CREATE TABLE IF NOT EXISTS respaldo_20261112_pre_mig.seguimiento_peticiones_visita AS
    TABLE public.seguimiento_peticiones_visita;

COMMIT;
*/

-- -----------------------------------------------------------------------------
-- 2. PROTOCOLO DE CONTENCION / RECUPERACION ANTE FALLOS POST-DESPLIEGUE
--    REGLA DE SEGURIDAD:
--      - Si ocurre un fallo DURANTE los bloques transaccionales BEGIN..COMMIT
--        (0002_reparar_tablas_faltantes.sql o 0003_rls_restrictivo_...),
--        PostgreSQL revierte atomicamente la transaccion completa sin dejar cambios parciales.
--      - Si la aplicacion falla DESPUES de aplicar cambios y AUN NO hay filas en las
--        tablas nuevas, priorizar contencion sin destruir datos existentes:
--          a) Desactivar temporalmente el acceso API a nuevas funciones (feature toggle / rollback de codigo)
--             o revocar permisos de insercion temporalmente.
--          b) Para revertir unicamente la migracion 0005 si es imprescindible y
--             peticion_posible_duplicado no tiene datos operativos que conservar:
--               python manage.py migrate peticiones 0004
--          c) NO eliminar ni truncar peticiones, notificaciones_peticiones ni historial_peticiones.
--          d) Para revertir RLS sin perder datos (si algun consumidor autorizado requiriera ajuste):
--             ajustar politicas RLS especificas en lugar de borrar tablas.
-- -----------------------------------------------------------------------------
