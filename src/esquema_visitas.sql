-- Qué sitios ya se visitaron, con qué resultado.
--
-- Sin esto, reanudar una corrida interrumpida significa volver a pedirle a
-- miles de servidores lo mismo que ya se les pidió hace una hora. Es la
-- diferencia entre reanudar y empezar de cero, y también entre ser un visitante
-- y ser una molestia.
--
-- Guarda también los fallos: un dominio que no respondió no hay que reintentarlo
-- en la misma tanda, y saber CUÁNTOS no respondieron es parte del informe.
create table if not exists enriquecimiento.sitio_visitado (
  prestador_id bigint primary key references reps.prestador(id) on delete cascade,
  dominio      text not null,
  resultado    text not null,          -- ok | sin_respuesta | robots
  personas     integer not null default 0,
  paginas      integer not null default 0,
  visitado_en  timestamptz not null default now()
);
create index if not exists sitio_visitado_res_idx on enriquecimiento.sitio_visitado(resultado);
