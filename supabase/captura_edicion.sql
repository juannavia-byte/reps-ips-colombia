-- Corregir y retirar lo capturado, sin perder el rastro de que existió.
--
-- Aplicar igual que el resto:
--   psql "$SUPABASE_DSN" -v ON_ERROR_STOP=1 -f supabase/captura_edicion.sql
--
-- No hace falta tocar «Exposed schemas»: las dos tablas ya viven en `captura`.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- QUÉ FALLABA
--
-- `captura.sql` dejó el esquema bien —`unique (nit, nombre_clave)` es una
-- persona por empresa— pero el formulario no podía llegar a él. Guardaba con
-- `Prefer: resolution=merge-duplicates` y SIN `on_conflict`, y PostgREST, sin
-- ese parámetro, resuelve el conflicto por la LLAVE PRIMARIA: `id`, un uuid
-- que se genera en cada insert y por tanto nunca choca. El ON CONFLICT no
-- entraba nunca, el INSERT seguía, y era el `unique` el que lo paraba con un
-- 23505 en la cara de quien estaba escribiendo.
--
-- Resultado visto en Junical Medical: la misma persona que es representante
-- legal y gerente general no se podía guardar dos veces —la segunda reventaba—
-- y si el nombre se teclaba distinto («Yuly Martínez» y «Yuly Andrea
-- Martínez») las dos grafías pasaban el `unique` y quedaban dos filas para un
-- solo ser humano.
--
-- Eso se arregla en el cliente (`on_conflict=nit,nombre_clave`). Lo que se
-- arregla acá es lo otro que hacía falta para que sea editable de verdad.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- POR QUÉ `retirado` Y NO UN DELETE
--
-- Un canal mal escrito se corrige con UPDATE y eso ya se podía. Lo que no
-- había era forma de QUITAR: un teléfono que resultó no ser de esa persona, o
-- una de las dos filas duplicadas al fundirlas. Y DELETE no se concede a
-- nadie a propósito —está razonado en `captura.sql`: un borrado real es el que
-- pide alguien invocando habeas data, y tiene que pasar por la lista de
-- exclusión del motor, que es donde queda constancia de la solicitud.
--
-- Así que se retira sin borrar, igual que `bajado_en` marca lo bajado sin
-- borrarlo y que `empresa.estado='pendiente'` deja volver atrás sin un DELETE.
-- La fila sigue ahí para contestar «¿de dónde salió este dato?», y con quién y
-- cuándo lo retiró, que es una pregunta que también se hace.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- LOS `unique` NO SE VUELVEN PARCIALES
--
-- Sería tentador hacerlos `where retirado is false` para que retirar libere la
-- clave. No se hace: entonces el mismo NIT podría tener tres filas retiradas
-- de la misma persona y una viva, y `traer_captura.py` tendría que decidir
-- cuál gana. Con el `unique` entero, volver a capturar a alguien retirado es
-- el mismo upsert de siempre y lo único que hace es revivir su fila —que es
-- exactamente lo que quiere decir volver a escribir su nombre.
-- ─────────────────────────────────────────────────────────────────────────────

begin;

alter table captura.persona
  add column if not exists retirado     boolean not null default false,
  add column if not exists retirado_en  timestamptz,
  add column if not exists retirado_por uuid references auth.users(id);

alter table captura.canal
  add column if not exists retirado     boolean not null default false,
  add column if not exists retirado_en  timestamptz,
  add column if not exists retirado_por uuid references auth.users(id);

comment on column captura.persona.retirado is
  'Se quitó de la ficha sin borrar la fila. Lo pone el formulario al fundir '
  'dos capturas de la misma persona o al deshacer una mal hecha. '
  '`traer_captura.py` no lo baja, y si ya estaba bajado lo marca obsoleto.';
comment on column captura.canal.retirado is
  'Idem. Un canal que resultó no ser de esta persona, o que nunca existió.';

-- La fecha y el autor del retiro a mano se olvidan, y peor: un `retirado_en`
-- que manda el navegador es la hora del reloj de ese portátil. Con disparador
-- los dos campos dicen la verdad sin que el cliente tenga que acordarse, y
-- deshacer el retiro los limpia en vez de dejar una fecha que ya no describe
-- nada.
create or replace function captura.marcar_retiro()
returns trigger language plpgsql as $$
begin
  if new.retirado and not old.retirado then
    new.retirado_en  = now();
    new.retirado_por = auth.uid();
  elsif old.retirado and not new.retirado then
    new.retirado_en  = null;
    new.retirado_por = null;
  end if;
  return new;
end $$;

drop trigger if exists persona_retiro on captura.persona;
create trigger persona_retiro before update on captura.persona
  for each row execute function captura.marcar_retiro();

drop trigger if exists canal_retiro on captura.canal;
create trigger canal_retiro before update on captura.canal
  for each row execute function captura.marcar_retiro();

-- `canal` no tenía disparador de `actualizado` porque no tiene la columna. No
-- se le añade: la fecha que importa de un canal es la de su retiro, y ésa ya
-- la pone el de arriba.

-- Lo que el formulario pide en cada apertura de ficha es «los vivos de este
-- NIT». Sin esto son dos escaneos de tabla por persona abierta.
create index if not exists persona_vivas_idx on captura.persona(nit)
  where retirado is false;
create index if not exists canal_vivos_idx on captura.canal(persona_id)
  where retirado is false;

commit;
