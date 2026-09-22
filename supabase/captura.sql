-- Esquema `captura`: lo que el equipo escribe desde Ariad, empresa por empresa.
--
-- Aplicar con la conexión directa, igual que el resto:
--   SUPABASE_DSN='postgresql://postgres:CLAVE@db.XXXX.supabase.co:5432/postgres'
--   psql "$SUPABASE_DSN" -v ON_ERROR_STOP=1 -f supabase/captura.sql
--
-- Y después, en Supabase: Settings > API > Exposed schemas, añadir «captura».
-- Sin eso PostgREST responde PGRST106 y el formulario no guarda nada.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- POR QUÉ UN ESQUEMA APARTE Y NO DENTRO DE `portal`
--
-- `portal.tablero` es una única fila de JSONB que el pipeline sobrescribe en
-- cada push. Si lo capturado viviera ahí dentro, el siguiente push lo borraría:
-- el payload se construye desde el Postgres local, que todavía no sabe nada de
-- lo que alguien acaba de teclear en el navegador.
--
-- Acá las filas son de la gente, no del pipeline. Nadie las sobrescribe, y
-- `traer_captura.py` las baja a `enriquecimiento.*` cuando toca.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- SE LIGA POR NIT, NO POR prestador_id
--
-- `push_supabase.sh` sube el esquema `reps` con un pg_restore, así que los id
-- coinciden hoy. Pero son `bigserial`: el día que el pipeline se reconstruya
-- desde cero, la misma clínica puede quedar con otro id, y lo capturado
-- apuntaría a otra empresa sin que nada avise. El NIT es el identificador que
-- no cambia, y es el que ya usa el puente de HubSpot.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- LA CONFIANZA NO SE GUARDA ACÁ
--
-- Acá sólo se guarda `confirmado`: si quien lo escribió lo vio o lo dedujo.
-- Traducir eso a estado y confianza (verificado/90, inferido/40) es decisión
-- del motor, y vive en un solo sitio —`traer_captura.py`— para que no haya dos
-- respuestas distintas a la misma pregunta según por dónde entró el dato.
-- ─────────────────────────────────────────────────────────────────────────────

begin;

create schema if not exists captura;

-- ── Personas ────────────────────────────────────────────────────────────────
create table if not exists captura.persona (
  id            uuid primary key default gen_random_uuid(),

  nit           text not null,
  -- Tokens del nombre, sin tildes, en minúscula y ORDENADOS. Lo calcula el
  -- navegador con la misma regla que `enriquecer.clave_nombre`, para que
  -- «ANA LUCIA RUIZ» y «RUIZ ANA LUCIA» sean una sola persona y no dos.
  nombre_clave  text not null,
  nombre        text not null,
  cargo         text,

  -- true: se vio publicado o lo dijeron. false: se dedujo.
  confirmado    boolean not null default true,
  fuente        text,
  notas         text,

  -- Quién lo escribió. No es auditoría por desconfianza: es lo que permite
  -- responder «¿de dónde salió este dato?» cuando alguien pregunte por qué
  -- tenemos el suyo, que es exactamente lo que la Ley 1581 obliga a poder
  -- contestar.
  creado_por    uuid not null default auth.uid() references auth.users(id),
  creado        timestamptz not null default now(),
  actualizado   timestamptz not null default now(),

  -- Bajado ya a enriquecimiento.* por traer_captura.py. No se borra al bajar:
  -- si se borrara, dos corridas seguidas no podrían distinguir «ya lo bajé» de
  -- «nunca existió», y se perdería el rastro de quién capturó qué.
  bajado_en     timestamptz,

  unique (nit, nombre_clave)
);

create index if not exists persona_nit_idx on captura.persona(nit);
create index if not exists persona_pendiente_idx on captura.persona(bajado_en)
  where bajado_en is null;

-- ── Canales ─────────────────────────────────────────────────────────────────
create table if not exists captura.canal (
  id            uuid primary key default gen_random_uuid(),

  -- Nulo cuando el canal es de la empresa y no de nadie en particular: el
  -- conmutador y el buzón de contratación existen sin que se sepa quién los
  -- atiende, y son perfectamente utilizables.
  persona_id    uuid references captura.persona(id) on delete cascade,
  nit           text not null,

  tipo          text not null
                check (tipo in ('correo','telefono','whatsapp','linkedin','web',
                                'facebook','instagram','x','tiktok','telegram')),
  valor         text not null,
  valor_norm    text not null,
  ambito        text not null default 'profesional'
                check (ambito in ('profesional','area','personal')),

  confirmado    boolean not null default true,
  creado_por    uuid not null default auth.uid() references auth.users(id),
  creado        timestamptz not null default now(),
  bajado_en     timestamptz,

  unique (nit, tipo, valor_norm)
);

create index if not exists canal_persona_idx on captura.canal(persona_id);
create index if not exists canal_nit_idx on captura.canal(nit);
create index if not exists canal_pendiente_idx on captura.canal(bajado_en)
  where bajado_en is null;

-- `actualizado` a mano se olvida. Con disparador, la fecha dice la verdad
-- aunque quien escribió el UPDATE no se acordara del campo.
create or replace function captura.marcar_actualizado()
returns trigger language plpgsql as $$
begin
  new.actualizado = now();
  return new;
end $$;

drop trigger if exists persona_actualizado on captura.persona;
create trigger persona_actualizado before update on captura.persona
  for each row execute function captura.marcar_actualizado();

-- ── Acceso ──────────────────────────────────────────────────────────────────
--
-- Mismo criterio que `reps` y `portal`: con sesión se entra, sin sesión no.
-- El equipo de la agencia trabaja sobre las mismas cuentas, así que todos ven
-- y editan todo; `creado_por` deja el rastro de quién puso cada cosa.
--
-- DELETE no se concede a nadie. Un contacto mal escrito se corrige con UPDATE,
-- y un borrado real —el que pide alguien invocando habeas data— tiene que
-- pasar por la lista de exclusión del motor, que es donde queda constancia de
-- la solicitud. Un DELETE desde el navegador borraría el dato sin dejar
-- registro de que alguien lo pidió.
grant usage on schema captura to authenticated;
grant select, insert, update on captura.persona to authenticated;
grant select, insert, update on captura.canal   to authenticated;

alter table captura.persona enable row level security;
alter table captura.canal   enable row level security;

drop policy if exists persona_leer     on captura.persona;
drop policy if exists persona_escribir on captura.persona;
drop policy if exists persona_corregir on captura.persona;
create policy persona_leer     on captura.persona for select to authenticated
  using (auth.uid() is not null);
create policy persona_escribir on captura.persona for insert to authenticated
  with check (auth.uid() is not null and creado_por = auth.uid());
create policy persona_corregir on captura.persona for update to authenticated
  using (auth.uid() is not null) with check (auth.uid() is not null);

drop policy if exists canal_leer     on captura.canal;
drop policy if exists canal_escribir on captura.canal;
drop policy if exists canal_corregir on captura.canal;
create policy canal_leer     on captura.canal for select to authenticated
  using (auth.uid() is not null);
create policy canal_escribir on captura.canal for insert to authenticated
  with check (auth.uid() is not null and creado_por = auth.uid());
create policy canal_corregir on captura.canal for update to authenticated
  using (auth.uid() is not null) with check (auth.uid() is not null);

-- `anon` es la clave que viaja al navegador y la ve cualquiera que abra el
-- inspector. Sin esta línea, una sesión no es necesaria para leer: basta la
-- clave pública. Es exactamente el agujero que tenía gh-pages, en otra forma.
revoke all on all tables in schema captura from anon;
revoke usage on schema captura from anon;

commit;
