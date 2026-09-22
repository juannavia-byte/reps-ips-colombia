-- Estado de trabajo por empresa: qué ya se revisó y qué falta.
--
-- Aplicar igual que el anterior:
--   psql "$SUPABASE_DSN" -v ON_ERROR_STOP=1 -f supabase/captura_avance.sql
--
-- No hace falta tocar «Exposed schemas» otra vez: la tabla entra en `captura`,
-- que ya está expuesto.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- POR QUÉ NO BASTA CON CONTAR LAS PERSONAS CAPTURADAS
--
-- Una empresa con contactos capturados está hecha, y eso se sabe solo. El
-- problema es la otra: la que se abre, se busca en su web, en Google y en el
-- directorio, y no da nada. Sin dónde anotarlo se ve idéntica a una que nadie
-- ha tocado, y se vuelve a trabajar dentro de dos semanas.
--
-- Media hora de búsqueda infructuosa es un resultado. Esta tabla lo guarda.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- «pendiente» ES UN ESTADO Y NO UNA FILA AUSENTE
--
-- Desmarcar una empresa tendría que ser un DELETE, y acá nadie tiene DELETE —
-- por la misma razón que en `persona` y `canal`. Con `pendiente` como estado
-- explícito, volver atrás es un UPDATE y queda el rastro de quién y cuándo.
-- ─────────────────────────────────────────────────────────────────────────────

begin;

create table if not exists captura.empresa (
  -- El NIT es la clave, como en el resto del esquema: los id de `reps` son
  -- bigserial y cambian si el pipeline se reconstruye desde cero.
  nit          text primary key,

  estado       text not null default 'pendiente'
               check (estado in ('pendiente','revisada','descartada')),

  -- Por qué no dio nada, o por qué se descarta. Es lo que evita repetir la
  -- misma búsqueda: «sin web, teléfono no contesta» ahorra la próxima media
  -- hora mucho más que el estado solo.
  nota         text,

  revisado_por uuid not null default auth.uid() references auth.users(id),
  actualizado  timestamptz not null default now()
);

create index if not exists empresa_estado_idx on captura.empresa(estado)
  where estado <> 'pendiente';

comment on table captura.empresa is
  'Estado de trabajo por empresa. «revisada» significa que se buscó y no se '
  'encontró a nadie, que es distinto de no haberla mirado. «descartada» es '
  'que no interesa como cuenta.';

drop trigger if exists empresa_actualizado on captura.empresa;
create trigger empresa_actualizado before update on captura.empresa
  for each row execute function captura.marcar_actualizado();

-- ── Acceso ──────────────────────────────────────────────────────────────────
-- Mismo criterio que el resto del esquema: con sesión se entra, sin sesión no,
-- y DELETE no lo tiene nadie.
grant select, insert, update on captura.empresa to authenticated;

alter table captura.empresa enable row level security;

drop policy if exists empresa_leer     on captura.empresa;
drop policy if exists empresa_escribir on captura.empresa;
drop policy if exists empresa_corregir on captura.empresa;
create policy empresa_leer     on captura.empresa for select to authenticated
  using (auth.uid() is not null);
create policy empresa_escribir on captura.empresa for insert to authenticated
  with check (auth.uid() is not null and revisado_por = auth.uid());
create policy empresa_corregir on captura.empresa for update to authenticated
  using (auth.uid() is not null) with check (auth.uid() is not null);

revoke all on captura.empresa from anon;

commit;
