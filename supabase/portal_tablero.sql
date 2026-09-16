-- Esquema `portal`: lo que el sitio de Proactivos sirve a sus usuarios.
--
-- Va aparte de `reps` a propósito. `reps` es el resultado del pipeline —tablas
-- normalizadas, con sus políticas ya aplicadas y probadas— y no se toca. Acá
-- vive una sola cosa: el payload que la herramienta necesita para arrancar.
--
-- Por qué un único JSONB y no 50 columnas tipadas:
--
--  * La herramienta (tablero/index.html) espera exactamente
--    `window.DATOS = {generado, cols, rows, pesos_icp}`. Guardar el payload tal
--    cual hace que la fidelidad sea trivial de verificar: lo que entra es lo
--    que sale. Con 50 columnas tipadas habría que mantener el mapeo a mano y
--    cada columna nueva del pipeline sería una migración.
--
--  * Se sirve en UNA petición. PostgREST corta las respuestas en 1000 filas, y
--    con 10.652 filas habría que paginar en el cliente — el mismo corte
--    silencioso que ya mordió en rc-medica-platform (ver su paginar.ts).
--    Con una fila no hay corte que pueda pasar desapercibido.
--
-- La frescura no se pierde: estos datos solo cambian cuando corre el pipeline
-- (la extracción de REPS es periódica, no continua). Consultar `reps` en vivo
-- no daría un dato más nuevo que el último `push_tablero.py`.

begin;

create schema if not exists portal;

create table if not exists portal.tablero (
  -- Una sola fila, siempre. El check impide que un push mal hecho deje dos
  -- payloads y que el sitio sirva uno u otro según el orden de lectura.
  id          smallint primary key default 1 check (id = 1),
  payload     jsonb    not null,
  filas       integer  not null,
  generado    date     not null,
  actualizado timestamptz not null default now()
);

comment on table portal.tablero is
  'Payload de la herramienta de prospección. Lo escribe src/push_tablero.py '
  'desde el Postgres local; el sitio lo lee con la sesión del usuario.';

-- Mismo criterio de acceso que `reps`: cualquiera con sesión iniciada lo lee,
-- nadie sin sesión. La escritura no la hace la aplicación sino el pipeline,
-- que entra con la conexión directa y no pasa por estas políticas.
grant usage on schema portal to authenticated;
grant select on portal.tablero to authenticated;

alter table portal.tablero enable row level security;
drop policy if exists authenticated_select on portal.tablero;
create policy authenticated_select on portal.tablero
  for select to authenticated using (auth.uid() is not null);

revoke all on all tables in schema portal from anon;
revoke usage on schema portal from anon;

commit;
