-- Lectura del esquema `reps` para usuarios logueados de la plataforma de RC.
--
-- Ambas plataformas viven en el mismo proyecto de Supabase
-- (hpwhgpeuthuovqzivdsp), así que la sesión que ya emite el login de RC sirve
-- tal cual: no hay que compartir nada entre proyectos.
--
-- Decisiones:
--
--  * Solo SELECT. La convención de `public` es `authenticated_all` con ALL,
--    pero esta base se construye desde el pipeline local y se reemplaza
--    entera en cada carga; que la aplicación pueda escribirla solo abre la
--    puerta a divergencias silenciosas contra el origen.
--
--  * `TO authenticated` Y `auth.uid() IS NOT NULL`. Lo primero sigue la
--    convención de RC; lo segundo es la condición pedida explícitamente. Con
--    un JWT de rol `authenticated` pero sin claim `sub`, auth.uid() devuelve
--    NULL y la fila no pasa. Es más estricto que cualquiera de los dos solo.
--
--  * El GRANT es tan necesario como el RLS. RLS filtra filas, pero sin
--    GRANT el rol ni siquiera puede mirar la tabla. Faltando uno de los dos
--    esto no funciona, y por razones distintas.
--
--  * `anon` no recibe nada. Queda dicho de forma explícita al final para que
--    un GRANT futuro sobre el esquema no lo arrastre por descuido.

begin;

-- 1. Acceso al esquema. Sin esto, el RLS no llega ni a evaluarse.
grant usage on schema reps to authenticated;
grant select on all tables in schema reps to authenticated;

-- Las tablas que se creen después heredan el permiso; si no, cada recarga
-- del pipeline dejaría tablas nuevas invisibles y el fallo aparecería tarde.
alter default privileges in schema reps grant select on tables to authenticated;

-- 2. RLS + política en las doce tablas base.
do $$
declare t text;
begin
  for t in
    select tablename from pg_tables where schemaname = 'reps'
  loop
    execute format('alter table reps.%I enable row level security', t);
    execute format('drop policy if exists authenticated_select on reps.%I', t);
    execute format(
      'create policy authenticated_select on reps.%I '
      'for select to authenticated using (auth.uid() is not null)', t);
  end loop;
end $$;

-- 3. La vista debe respetar el RLS de las tablas que lee.
-- Sin security_invoker corre con los permisos de quien la creó (postgres) y
-- devuelve todo aunque las políticas de abajo digan que no. Hoy el resultado
-- coincidiría, pero deja de coincidir en cuanto alguien restrinja una tabla.
alter view reps.v_prestador_completo set (security_invoker = true);

-- 4. Nada para quien no ha iniciado sesión.
revoke all on all tables in schema reps from anon;
revoke usage on schema reps from anon;

commit;
