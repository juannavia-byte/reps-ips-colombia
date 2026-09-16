-- ============================================================================
-- Universo de prestadores de servicios de salud de Colombia
-- Fuentes: REPS (MinSalud, consulta pública) + Supersalud (estados financieros)
--
-- GRANO DE LA TABLA RAÍZ
-- ----------------------
-- `prestador` se identifica por (tipo_identificacion, numero_identificacion),
-- NO por NIT solo. Con el universo REPS completo entran 54.657 sedes de
-- profesionales independientes que se identifican con cédula, y un número de
-- cédula puede coincidir con un NIT. Deduplicar por el número solo fusionaría
-- entidades distintas.
--
-- Un mismo documento tiene VARIOS códigos de habilitación: uno por territorio
-- donde se registra. Medido sobre clase IPS: 9.079 documentos -> 10.939 códigos
-- -> 19.551 sedes. Por eso `registro_habilitacion` es una tabla aparte y no una
-- columna de `prestador`.
--
-- NINGUNA fila de REPS se descarta por no tener contraparte financiera.
-- ============================================================================

DROP SCHEMA IF EXISTS reps CASCADE;
CREATE SCHEMA reps;
SET search_path TO reps, public;

-- ---------------------------------------------------------------- linaje ---

CREATE TABLE extraccion (
    id                      serial PRIMARY KEY,
    fuente                  text        NOT NULL,   -- 'reps' | 'supersalud'
    recurso                 text        NOT NULL,   -- 'prestadores', 'privadas_2021', ...
    url                     text        NOT NULL,
    extraido_en             timestamptz NOT NULL,
    fecha_corte_declarada   text,                   -- lo que dice la propia fuente
    archivo                 text,
    sha256                  text,
    tamano_bytes            bigint,
    filas_crudas            integer,
    separador               text,
    notas                   text
);
COMMENT ON TABLE  extraccion IS 'Una fila por archivo descargado. Todo dato del modelo apunta aquí.';
COMMENT ON COLUMN extraccion.fecha_corte_declarada IS
    'Corte que declara la fuente, no la fecha de descarga. En REPS viene dentro del CSV.';

-- ------------------------------------------------------------ prestadores ---

CREATE TABLE prestador (
    id                      bigserial PRIMARY KEY,
    tipo_identificacion     text NOT NULL,          -- NI | CC | CE | PT
    numero_identificacion   text NOT NULL,
    digito_verificacion     text,
    razon_social            text NOT NULL,          -- canónica: la del registro más reciente
    clase_persona           text,                   -- NATURAL | JURIDICO
    naturaleza_juridica     text,                   -- Privada | Pública | Mixta
    es_ese                  boolean,
    clase_prestador         text,                   -- IPS | Profesional Independiente | ...
    es_ips                  boolean NOT NULL,       -- atajo: clase_prestador = IPS
    -- Sin fuente pública a nivel de entidad. Ver docs/00-fuentes.md §5.
    numero_empleados        integer,
    extraccion_id           integer NOT NULL REFERENCES extraccion(id),
    UNIQUE (tipo_identificacion, numero_identificacion)
);
COMMENT ON COLUMN prestador.numero_empleados IS
    'SIEMPRE NULL. Ninguna fuente pública colombiana lo expone por IPS: ni REPS, ni '
    'Supersalud, ni RUES, ni PILA. El proxy de tamaño es sede_capacidad.cantidad.';

CREATE TABLE registro_habilitacion (
    id                      bigserial PRIMARY KEY,
    prestador_id            bigint NOT NULL REFERENCES prestador(id) ON DELETE CASCADE,
    codigo_habilitacion     text NOT NULL UNIQUE,
    nombre_prestador        text,
    representante_legal     text,
    gerente                 text,
    nivel_atencion          smallint,
    caracter_territorial    text,
    habilitado              boolean,
    fecha_radicacion        date,
    fecha_vencimiento       date,
    fecha_cierre            date,
    departamento            text,
    municipio               text,
    numero_sede_principal   text,
    telefono                text,
    email                   text,
    direccion               text,
    extraccion_id           integer NOT NULL REFERENCES extraccion(id)
);
COMMENT ON TABLE registro_habilitacion IS
    'Un código de habilitación. Un prestador puede tener hasta 25 (uno por territorio).';

-- ------------------------------------------------------------------ sedes ---

-- OJO con la llave. En el export de sedes, `codigo_prestador` es el código del
-- prestador y `codigo_habilitacion` es el código PROPIO DE LA SEDE: difieren en
-- 6.333 de 77.011 filas. La pareja (codigo_prestador, numero_sede) tiene 25
-- duplicados; (codigo_habilitacion_sede, numero_sede) es única. Servicios y
-- capacidad referencian la sede por su código propio, no por el del prestador.
CREATE TABLE sede (
    id                      bigserial PRIMARY KEY,
    registro_id             bigint NOT NULL REFERENCES registro_habilitacion(id) ON DELETE CASCADE,
    codigo_habilitacion_sede text NOT NULL,
    numero_sede             text NOT NULL,
    nombre                  text,
    es_principal            boolean,
    departamento            text,
    municipio               text,
    direccion               text,
    barrio                  text,
    zona                    text,                   -- URBANA | RURAL
    centro_poblado          text,
    telefono                text,
    email                   text,
    fecha_apertura          date,
    fecha_cierre            date,
    habilitada              boolean,
    horarios                jsonb,                  -- {lunes: '07 a 17', ...}
    extraccion_id           integer NOT NULL REFERENCES extraccion(id),
    UNIQUE (codigo_habilitacion_sede, numero_sede)
);

CREATE TABLE sede_servicio (
    id                      bigserial PRIMARY KEY,
    sede_id                 bigint NOT NULL REFERENCES sede(id) ON DELETE CASCADE,
    grupo_codigo            text,
    grupo_nombre            text,
    servicio_codigo         text,
    servicio_nombre         text,
    complejidad_baja        boolean,
    complejidad_media       boolean,
    complejidad_alta        boolean,
    ambulatorio             boolean,
    hospitalario            boolean,
    unidad_movil            boolean,
    domiciliario            boolean,
    centro_referencia       boolean,
    institucion_remisora    boolean,
    modalidades             jsonb,                  -- intramural, telemedicina, extramural…
    especificidades         jsonb,                  -- oncológico, trasplantes, quemados…
    numero_distintivo       text,
    -- El disparador comercial: habilitar un servicio nuevo es un hecho público y fechado.
    fecha_apertura          date,
    fecha_cierre            date,
    version_norma           text,
    extraccion_id           integer NOT NULL REFERENCES extraccion(id)
);
COMMENT ON COLUMN sede_servicio.fecha_apertura IS
    'Fecha de habilitación del servicio en esa sede. Es el campo que el dataset '
    'abierto de datos.gov.co no expone.';

CREATE TABLE sede_capacidad (
    id                      bigserial PRIMARY KEY,
    sede_id                 bigint NOT NULL REFERENCES sede(id) ON DELETE CASCADE,
    grupo_capacidad         text,                   -- CAMAS | SALAS | CONSULTORIOS | AMBULANCIAS…
    concepto_codigo         text,
    concepto_nombre         text,                   -- 'Intensiva Adultos', 'Sala de Cirugía'…
    cantidad                integer,
    placa                   text,                   -- solo ambulancias
    modalidad               text,
    modelo                  text,
    numero_tarjeta          text,
    extraccion_id           integer NOT NULL REFERENCES extraccion(id)
);
COMMENT ON TABLE sede_capacidad IS
    'El único proxy de tamaño disponible en fuente pública. Reemplaza a "número de empleados".';

-- ------------------------------------------------------------- financiero ---

CREATE TABLE prestador_financiero (
    id                      bigserial PRIMARY KEY,
    prestador_id            bigint REFERENCES prestador(id) ON DELETE CASCADE,
    -- Se conserva aunque no haya match, para poder medir y auditar la tasa de cruce.
    nit_reportado           text NOT NULL,
    razon_social_reportada  text,
    vigencia                smallint NOT NULL,
    origen                  text NOT NULL,          -- privada_g1|privada_g2|privada_g3|ese
    codigo_habilitacion_rep text,                   -- solo lo traen las ESE
    nivel                   text,
    departamento            text,
    municipio               text,
    -- Los tres grupos NIIF tienen planes de cuentas distintos (390/351/266 columnas).
    -- Aplanarlos a columnas comunes obligaría a inventar equivalencias.
    cuentas                 jsonb NOT NULL,
    -- Las cinco cifras comparables entre grupos, promovidas para poder consultarlas.
    activos                 numeric(20,2),
    pasivos                 numeric(20,2),
    patrimonio              numeric(20,2),
    ingresos                numeric(20,2),
    costos                  numeric(20,2),
    extraccion_id           integer NOT NULL REFERENCES extraccion(id),
    UNIQUE (nit_reportado, vigencia, origen)
);

-- ----------------------------------------------------------------- calidad ---

CREATE TABLE qa_dedup (
    id                      bigserial PRIMARY KEY,
    tipo_identificacion     text,
    numero_identificacion   text,
    motivo                  text NOT NULL,
    codigos_fusionados      text[],
    razones_sociales        text[],
    razon_social_elegida    text,
    criterio                text
);
COMMENT ON TABLE qa_dedup IS 'Qué se fusionó y con qué criterio se eligió la razón social.';

CREATE TABLE qa_inconsistencia (
    id                      bigserial PRIMARY KEY,
    extraccion_id           integer REFERENCES extraccion(id),
    tabla                   text,
    clave                   text,
    tipo                    text NOT NULL,          -- nit_mal_formado, dv_invalido, ...
    detalle                 text,
    valor_crudo             text
);

CREATE INDEX ix_prestador_num      ON prestador (numero_identificacion);
CREATE INDEX ix_prestador_ips      ON prestador (es_ips) WHERE es_ips;
CREATE INDEX ix_registro_prest     ON registro_habilitacion (prestador_id);
CREATE INDEX ix_registro_depto     ON registro_habilitacion (departamento);
CREATE INDEX ix_sede_registro      ON sede (registro_id);
CREATE INDEX ix_sede_geo           ON sede (departamento, municipio);
CREATE INDEX ix_serv_sede          ON sede_servicio (sede_id);
CREATE INDEX ix_serv_apertura      ON sede_servicio (fecha_apertura);
CREATE INDEX ix_serv_nombre        ON sede_servicio (servicio_nombre);
CREATE INDEX ix_cap_sede           ON sede_capacidad (sede_id);
CREATE INDEX ix_cap_grupo          ON sede_capacidad (grupo_capacidad, concepto_nombre);
CREATE INDEX ix_fin_prestador      ON prestador_financiero (prestador_id);
CREATE INDEX ix_fin_nit            ON prestador_financiero (nit_reportado);

-- ------------------------------------------------------------------ vista ---

CREATE VIEW v_prestador_completo AS
SELECT
    p.id,
    p.tipo_identificacion,
    p.numero_identificacion,
    p.digito_verificacion,
    p.razon_social,
    p.clase_prestador,
    p.es_ips,
    p.naturaleza_juridica,
    p.es_ese,
    p.numero_empleados,
    (SELECT count(*) FROM registro_habilitacion r WHERE r.prestador_id = p.id)  AS n_codigos,
    (SELECT count(*) FROM sede s JOIN registro_habilitacion r ON r.id = s.registro_id
       WHERE r.prestador_id = p.id)                                            AS n_sedes,
    (SELECT count(*) FROM sede_servicio sv JOIN sede s ON s.id = sv.sede_id
       JOIN registro_habilitacion r ON r.id = s.registro_id
       WHERE r.prestador_id = p.id)                                            AS n_servicios,
    (SELECT coalesce(sum(c.cantidad),0) FROM sede_capacidad c JOIN sede s ON s.id = c.sede_id
       JOIN registro_habilitacion r ON r.id = s.registro_id
       WHERE r.prestador_id = p.id AND c.grupo_capacidad = 'CAMAS')            AS camas,
    (SELECT coalesce(sum(c.cantidad),0) FROM sede_capacidad c JOIN sede s ON s.id = c.sede_id
       JOIN registro_habilitacion r ON r.id = s.registro_id
       WHERE r.prestador_id = p.id AND c.concepto_nombre = 'Sala de Cirugía')  AS salas_cirugia,
    (SELECT coalesce(sum(c.cantidad),0) FROM sede_capacidad c JOIN sede s ON s.id = c.sede_id
       JOIN registro_habilitacion r ON r.id = s.registro_id
       WHERE r.prestador_id = p.id AND c.grupo_capacidad = 'CONSULTORIOS')     AS consultorios,
    (SELECT coalesce(sum(c.cantidad),0) FROM sede_capacidad c JOIN sede s ON s.id = c.sede_id
       JOIN registro_habilitacion r ON r.id = s.registro_id
       WHERE r.prestador_id = p.id AND c.grupo_capacidad = 'AMBULANCIAS')      AS ambulancias,
    f.vigencia      AS fin_vigencia,
    f.origen        AS fin_origen,
    f.activos       AS fin_activos,
    f.pasivos       AS fin_pasivos,
    f.patrimonio    AS fin_patrimonio,
    f.ingresos      AS fin_ingresos,
    (f.id IS NOT NULL) AS tiene_financiero
FROM prestador p
LEFT JOIN prestador_financiero f ON f.prestador_id = p.id;

COMMENT ON VIEW v_prestador_completo IS
    'Una fila por prestador con sedes, servicios y capacidad agregados. '
    'LEFT JOIN al financiero: quien no cruza queda con NULL, no se pierde.';
