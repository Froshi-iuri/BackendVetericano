-- =============================================================================
-- VETERICANO - Endurecimiento RLS restrictivo para peticion_foto_autorizada
-- Tabla objetivo:
--   - public.peticion_foto_autorizada
-- Comportamiento garantizado:
--   - Activa RLS (ENABLE ROW LEVEL SECURITY, sin FORCE para preservar al owner)
--   - Sin politicas permisivas para anon ni authenticated (bloqueo por defecto)
--   - Crea politica explicita unicamente para service_role (si el rol existe)
--   - Revoca privilegios heredados por DEFAULT_ACL a PUBLIC, anon y authenticated
--     sobre la tabla y su secuencia IDENTITY (si los roles existen)
--   - Django (conectado como rol postgres con rolbypassrls=true y owner) conserva
--     acceso completo SELECT/INSERT/UPDATE/DELETE
--   - 100% aditiva e idempotente (sin DROP, TRUNCATE ni DELETE)
-- =============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15s';

ALTER TABLE public."peticion_foto_autorizada" ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    tbl CONSTANT TEXT := 'peticion_foto_autorizada';
    pk_col CONSTANT TEXT := 'id_autorizacion';
    pol_name TEXT;
    seq_name TEXT;
BEGIN
    -- 1. Revocar acceso directo de PUBLIC y roles PostgREST (anon/authenticated)
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC;', tbl);

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon;', tbl);
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE format('REVOKE ALL ON TABLE public.%I FROM authenticated;', tbl);
    END IF;

    SELECT pg_get_serial_sequence(format('public.%I', tbl), pk_col) INTO seq_name;
    IF seq_name IS NOT NULL THEN
        EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM PUBLIC;', seq_name);
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM anon;', seq_name);
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM authenticated;', seq_name);
        END IF;
    END IF;

    -- 2. Politica explicita restrictiva/dedicada para service_role (si existe)
    --    Ninguna politica permite lectura/escritura a anon ni authenticated.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        pol_name := format('rls_%s_solo_service_role', tbl);
        IF NOT EXISTS (
            SELECT 1
            FROM pg_policies
            WHERE schemaname = 'public'
              AND tablename = tbl
              AND policyname = pol_name
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);',
                pol_name,
                tbl
            );
        END IF;
    END IF;
END $$;

COMMIT;
