-- =============================================================================
-- VETERICANO - Verificacion estrictamente SOLO LECTURA post-despliegue
-- Verifica estructura, FKs, CHECK/UNIQUE, indices, RLS, privilegios y conteos
-- =============================================================================

BEGIN READ ONLY;

-- 1. Tablas y estado RLS en public
SELECT c.relname AS tabla,
       c.relrowsecurity AS rls_habilitado,
       c.relforcerowsecurity AS force_rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'r'
  AND c.relname IN (
      'historial_asignacion_peticion',
      'notificacion_juridico',
      'peticion_posible_duplicado',
      'historial_peticiones',
      'notificaciones_peticiones',
      'peticiones'
  )
ORDER BY c.relname;

-- 2. Restricciones (PK, FK, CHECK, UNIQUE) de las 3 tablas objetivo
SELECT c.relname AS tabla,
       con.conname AS restriccion,
       con.contype AS tipo,
       pg_get_constraintdef(con.oid) AS definicion
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN (
      'historial_asignacion_peticion',
      'notificacion_juridico',
      'peticion_posible_duplicado'
  )
ORDER BY c.relname, con.contype, con.conname;

-- 3. Indices creados
SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN (
      'historial_asignacion_peticion',
      'notificacion_juridico',
      'peticion_posible_duplicado'
  )
ORDER BY tablename, indexname;

-- 4. Politicas RLS activas
SELECT schemaname, tablename, policyname, permissive, roles, cmd
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN (
      'historial_asignacion_peticion',
      'notificacion_juridico',
      'peticion_posible_duplicado'
  )
ORDER BY tablename, policyname;

-- 5. Privilegios remanentes en table_privileges para anon/authenticated/postgres/service_role
SELECT table_name, grantee, string_agg(privilege_type, ', ' ORDER BY privilege_type) AS privilegios
FROM information_schema.table_privileges
WHERE table_schema = 'public'
  AND table_name IN (
      'historial_asignacion_peticion',
      'notificacion_juridico',
      'peticion_posible_duplicado'
  )
  AND grantee IN ('PUBLIC', 'anon', 'authenticated', 'postgres', 'service_role')
GROUP BY table_name, grantee
ORDER BY table_name, grantee;

-- 6. Registro de migraciones peticiones y users
SELECT id, app, name, applied
FROM public.django_migrations
WHERE app IN ('peticiones', 'users')
ORDER BY id;

-- 7. Conservacion de conteos de datos preexistentes
SELECT 'peticiones' AS tabla, COUNT(*) AS filas FROM public.peticiones
UNION ALL
SELECT 'notificaciones_peticiones', COUNT(*) FROM public.notificaciones_peticiones
UNION ALL
SELECT 'historial_peticiones', COUNT(*) FROM public.historial_peticiones
UNION ALL
SELECT 'seguimiento_peticiones_visita', COUNT(*) FROM public.seguimiento_peticiones_visita
UNION ALL
SELECT 'historial_asignacion_peticion', COUNT(*) FROM public.historial_asignacion_peticion
UNION ALL
SELECT 'notificacion_juridico', COUNT(*) FROM public.notificacion_juridico
UNION ALL
SELECT 'peticion_posible_duplicado', COUNT(*) FROM public.peticion_posible_duplicado;

ROLLBACK;
