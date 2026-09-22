-- Motor de enriquecimiento de contactos de Ariad.
--
-- Va en su propio esquema y no dentro de `reps` porque `reps` es el espejo del
-- Registro Especial de Prestadores y nada más. Acá vive lo que sale de cruzar
-- ese registro con RUES, SECOP y la web: son afirmaciones nuestras sobre
-- personas, con su fuente y su nivel de certeza, no un registro oficial.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- TRES TABLAS Y NO UNA, POR UNA RAZÓN CONCRETA
--
-- Una sola tabla «contactos» con columnas correo/telefono/linkedin obliga a
-- decidir, por cada fila, UNA confianza y UNA fecha de verificación para datos
-- que no valen lo mismo: el nombre puede venir confirmado por dos fuentes
-- oficiales y el correo ser una inferencia de patrón sin verificar. Metidos en
-- la misma fila, o se miente sobre el correo o se infravalora el nombre.
--
--   persona    quién es y qué cargo tiene
--   canal      cada vía de contacto, con SU propia confianza y estado
--   evidencia  de dónde salió cada afirmación, con el registro crudo
--
-- ─────────────────────────────────────────────────────────────────────────────
-- ALCANCE: TODA PERSONA CONFIRMADA, NO SOLO LOS TIERS
--
-- Se guarda cualquier persona de la que una fuente diga que trabaja ahí, con su
-- cargo literal. El tier es una ETIQUETA para ordenar el esfuerzo, no un filtro
-- de entrada: quien usa Ariad elige a quién contactar mirando la lista completa,
-- y un cargo que hoy parece irrelevante es la puerta de entrada de mañana.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- LEY 1581 DE 2012 (HABEAS DATA)
--
-- `ambito` distingue el dato de contacto PROFESIONAL (buzón de área, conmutador,
-- correo corporativo nominal) del dato PERSONAL (celular particular, correo
-- gmail/hotmail). No es una etiqueta decorativa: el segundo exige más cuidado y
-- es el primero que hay que poder suprimir ante una solicitud.
--
-- `base_licitud` queda escrita en cada canal para que, si alguien pregunta por
-- qué tenemos su dato, la respuesta esté en la fila y no en la memoria de nadie.
--
-- La tabla `exclusion` es la lista de no-contactar. Se consulta ANTES de
-- exportar, y por NIT o por dato, porque quien pide no ser contactado rara vez
-- sabe con qué identificador figura en nuestra base.
-- ─────────────────────────────────────────────────────────────────────────────

begin;

create schema if not exists enriquecimiento;

-- ── Personas ────────────────────────────────────────────────────────────────
create table if not exists enriquecimiento.persona (
  id                bigserial primary key,
  prestador_id      bigint not null references reps.prestador(id) on delete cascade,

  -- Clave de deduplicación. RUES escribe «APELLIDOS NOMBRES» y REPS «NOMBRES
  -- APELLIDOS», así que se normaliza a tokens sin tildes ORDENADOS: las dos
  -- grafías del mismo ser humano colapsan en la misma clave.
  nombre_clave      text not null,
  nombre            text not null,          -- como se muestra, la mejor grafía vista
  documento         text,                   -- cédula, cuando la fuente la da

  cargo             text,                   -- literal de la fuente, sin recortar
  cargo_categoria   text,                   -- etiqueta normalizada nuestra
  tier              smallint,               -- 1..4; null = relevante pero sin tier

  estado            text not null default 'inferido'
                    check (estado in ('verificado','inferido','obsoleto','sin_encontrar')),
  confianza         smallint not null default 0 check (confianza between 0 and 100),

  visto_primera_vez timestamptz not null default now(),
  ultima_verificacion timestamptz not null default now(),
  notas             text,

  unique (prestador_id, nombre_clave)
);

create index if not exists persona_prestador_idx on enriquecimiento.persona(prestador_id);
create index if not exists persona_tier_idx on enriquecimiento.persona(tier) where tier is not null;

comment on column enriquecimiento.persona.nombre_clave is
  'Tokens del nombre, sin tildes, en minúscula y ORDENADOS alfabéticamente. '
  'Hace que «GOMEZ DIAZ CARMEN IRENE» (RUES) y «CARMEN IRENE GOMEZ DIAZ» (REPS) '
  'sean la misma persona sin depender del orden de la fuente.';

comment on column enriquecimiento.persona.estado is
  'verificado: dos fuentes independientes coinciden. inferido: una sola fuente. '
  'obsoleto: alguna fuente indica que ya no está (matrícula cancelada, relevo). '
  'sin_encontrar: se buscó y no hay nadie.';

-- ── Canales de contacto ─────────────────────────────────────────────────────
create table if not exists enriquecimiento.canal (
  id             bigserial primary key,

  -- Un canal puede colgar de una persona o sólo de la empresa: el conmutador y
  -- el buzón de contratación existen sin que se sepa quién los atiende, y son
  -- perfectamente utilizables. Obligarlos a tener persona los perdería.
  persona_id     bigint references enriquecimiento.persona(id) on delete cascade,
  prestador_id   bigint not null references reps.prestador(id) on delete cascade,

  -- El teléfono fijo y el celular son ambos `telefono`: el tipo dice POR DÓNDE
  -- se contacta y `ambito` dice quién atiende del otro lado. Las redes entran
  -- porque la IPS pequeña de municipio publica un Facebook mucho antes que un
  -- LinkedIn. Ver src/migracion_canales_sociales.sql.
  tipo           text not null
                 check (tipo in ('correo','telefono','whatsapp','linkedin','web',
                                 'facebook','instagram','x','tiktok','telegram')),
  valor          text not null,
  valor_norm     text not null,           -- para deduplicar sin repetir la limpieza

  ambito         text not null default 'profesional'
                 check (ambito in ('profesional','area','personal')),

  estado         text not null default 'inferido'
                 check (estado in ('verificado','inferido','obsoleto')),
  confianza      smallint not null default 0 check (confianza between 0 and 100),

  base_licitud   text not null default 'interes_legitimo_b2b',
  ultima_verificacion timestamptz not null default now(),

  unique (prestador_id, tipo, valor_norm)
);

create index if not exists canal_persona_idx on enriquecimiento.canal(persona_id);
create index if not exists canal_prestador_idx on enriquecimiento.canal(prestador_id);

comment on column enriquecimiento.canal.ambito is
  'profesional: correo corporativo nominal o directo de trabajo. '
  'area: buzón funcional (contratacion@, gerencia@) — atendido, pero no de una '
  'persona; no se trata como contacto nominal en una secuencia. '
  'personal: gmail/hotmail o celular particular — el que más cuidado exige.';

comment on column enriquecimiento.canal.estado is
  'verificado: comprobado contra algo (MX del dominio, coincidencia entre dos '
  'fuentes). inferido: generado por patrón o visto en una sola fuente sin '
  'comprobar. NUNCA se marca verificado un correo del que sólo se validó la '
  'sintaxis.';

-- ── Evidencia ───────────────────────────────────────────────────────────────
create table if not exists enriquecimiento.evidencia (
  id           bigserial primary key,
  persona_id   bigint references enriquecimiento.persona(id) on delete cascade,
  canal_id     bigint references enriquecimiento.canal(id) on delete cascade,
  fuente       text not null,        -- reps | rues | secop_proveedores | secop_contratos | web | patron | manual
  referencia   text,                 -- URL, dataset+id, o el dominio consultado
  extraido_en  timestamptz not null default now(),
  crudo        jsonb,                -- el registro tal como vino
  check (persona_id is not null or canal_id is not null)
);

create index if not exists evidencia_persona_idx on enriquecimiento.evidencia(persona_id);
create index if not exists evidencia_canal_idx on enriquecimiento.evidencia(canal_id);

-- ── Exclusiones (habeas data) ───────────────────────────────────────────────
create table if not exists enriquecimiento.exclusion (
  id           bigserial primary key,
  -- Cualquiera de los tres basta. Quien pide no ser contactado dice «soy
  -- fulano del hospital tal» o da su correo; casi nunca sabe su id interno.
  prestador_id bigint references reps.prestador(id) on delete cascade,
  nombre_clave text,
  valor_norm   text,
  motivo       text,
  solicitado_en timestamptz not null default now(),
  check (prestador_id is not null or nombre_clave is not null or valor_norm is not null)
);

-- ── Bitácora de corridas ────────────────────────────────────────────────────
create table if not exists enriquecimiento.corrida (
  id            bigserial primary key,
  paso          text not null,
  alcance       text,                 -- 'prioridad=alta', 'todas', …
  empresas      integer,
  personas_nuevas integer,
  canales_nuevos  integer,
  peticiones    integer,
  costo_usd     numeric(10,4) not null default 0,
  iniciada      timestamptz not null default now(),
  terminada     timestamptz,
  notas         text
);

comment on table enriquecimiento.corrida is
  'Una fila por ejecución. `costo_usd` existe aunque hoy todo sea gratis: sin el '
  'campo desde el principio, el día que entre una fuente paga no habría con qué '
  'comparar el costo por contacto contra las corridas anteriores.';

commit;
