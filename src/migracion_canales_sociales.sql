-- Amplía los tipos de canal de contacto.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- POR QUÉ: EL PUENTE DESDE HUBSPOT ESTABA ROTO PARA REDES
--
-- `traer_hubspot.py` mapea desde el primer día `hs_facebookid` → `facebook` y
-- `proa_instagram` → `instagram`, pero el CHECK original de `canal.tipo` sólo
-- aceptaba cinco valores y ninguno era ésos. Cualquier contacto de HubSpot con
-- Facebook o Instagram lleno reventaba el INSERT contra la restricción. Nunca
-- se notó porque hasta hoy nadie había llenado esos campos.
--
-- No es una función nueva: es cerrar el hueco entre lo que el código ya
-- intentaba escribir y lo que la tabla dejaba entrar.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- QUÉ ENTRA Y QUÉ NO
--
-- Entran las vías por las que efectivamente se le escribe a alguien y que en
-- el tejido de prestadores colombianos aparecen de verdad: la IPS familiar de
-- municipio publica un Facebook y un WhatsApp mucho antes que un LinkedIn.
--
--   facebook · instagram · x · tiktok · telegram
--
-- NO entra `youtube`: un canal de video es presencia, no vía de contacto, y
-- meterlo aquí obligaría a que el exportador decida qué hacer con una columna
-- por la que nadie va a escribir.
--
-- NO entra `telefono_fijo` como tipo aparte. El tipo dice POR DÓNDE se
-- contacta; quién atiende del otro lado lo dice `ambito`: un conmutador es
-- `telefono`/`area` y un celular es `telefono`/`personal`. Es exactamente la
-- distinción que ya hace el puente de HubSpot con `phone` y `mobilephone`, y
-- partir el tipo la duplicaría en dos sitios que se van a desincronizar.
-- ─────────────────────────────────────────────────────────────────────────────

begin;

-- El nombre del constraint lo generó Postgres al crear la tabla. Se busca por
-- tabla y columna en vez de asumir `canal_tipo_check`: si esta base se creó
-- con otra versión, el nombre podría diferir y el DROP fallaría en silencio
-- dejando el CHECK viejo puesto.
do $$
declare
  nombre text;
begin
  select con.conname into nombre
    from pg_constraint con
    join pg_class rel on rel.oid = con.conrelid
    join pg_namespace nsp on nsp.oid = rel.relnamespace
   where nsp.nspname = 'enriquecimiento'
     and rel.relname = 'canal'
     and con.contype = 'c'
     and pg_get_constraintdef(con.oid) like '%tipo%';
  if nombre is not null then
    execute format('alter table enriquecimiento.canal drop constraint %I', nombre);
  end if;
end $$;

alter table enriquecimiento.canal
  add constraint canal_tipo_check
  check (tipo in ('correo','telefono','whatsapp','linkedin','web',
                  'facebook','instagram','x','tiktok','telegram'));

comment on column enriquecimiento.canal.tipo is
  'Por dónde se contacta, no qué etiqueta le puso la fuente. El teléfono fijo '
  'y el celular son ambos `telefono`: los distingue `ambito` (area vs '
  'personal). Las redes entran porque la IPS pequeña de municipio publica un '
  'Facebook y un WhatsApp mucho antes que un LinkedIn.';

-- `evidencia.fuente` es texto libre, sin CHECK. El comentario era la única
-- documentación de los valores en uso y no mencionaba `manual`, que el puente
-- de HubSpot escribe desde que existe y ahora también escribe la hoja.
comment on column enriquecimiento.evidencia.fuente is
  'reps | rues | secop_proveedores | secop_contratos | web | patron | manual. '
  '`manual` es todo dato que puso una persona: hoja de captura o HubSpot.';

commit;
