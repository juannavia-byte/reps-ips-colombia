# Reporte de construcción · universo de prestadores de salud de Colombia

Generado: 2026-09-16T05:37:14+00:00
Base: PostgreSQL 16, esquema `reps`.

## 1. Volúmenes cargados

| Tabla | Filas |
|---|---|
| filas financieras | 6,634 |
| códigos de habilitación | 61,177 |
| prestadores (entidades únicas) | 57,663 |
| sedes | 77,009 |
| registros de capacidad | 97,549 |
| servicios habilitados | 228,039 |

### Por clase de prestador

| Clase | Entidades | Códigos |
|---|---|---|
| Profesional Independiente | 47,011 | 48,391 |
| Instituciones Prestadoras de Servicios de Salud - IPS | 8,972 | 10,783 |
| Objeto Social Diferente a la Prestación de Servicios de Salud | 1,242 | 1,474 |
| Transporte Especial de Pacientes | 438 | 529 |

### Por naturaleza jurídica

| Naturaleza | Entidades |
|---|---|
| Privada | 56,587 |
| Pública | 1,051 |
| Mixta | 25 |


## 2. Cruce con Supersalud

Vigencia cargada: **2021** — la última que publica la Superintendencia.

| Origen | Filas | Cruzadas | Sin match | % cruce |
|---|---|---|---|---|
| ese | 919 | 916 | 3 | 99.7 |
| privada_g1 | 125 | 105 | 20 | 84.0 |
| privada_g2 | 4,097 | 3,442 | 655 | 84.0 |
| privada_g3 | 1,493 | 1,158 | 335 | 77.6 |

- Filas financieras totales: **6,634**, de las cuales **5,621** cruzaron contra el REPS (**84.7 %**).
- Prestadores del REPS con estado financiero: **5,620** de 57,663 (9.75 %).
- Prestadores **sin** estado financiero: **52,043**. Quedan con los campos financieros en NULL; **ninguno se descartó**.
- Las 1,013 filas sin contraparte se conservan con `prestador_id` NULL y quedan registradas en `qa_inconsistencia` con tipo `sin_match_reps`.

**Por qué no cruza el 100 %:** el archivo de Supersalud es de 2021 y el REPS es de hoy.
Una IPS que cerró, se fusionó o perdió la habilitación entre 2021 y 2026 aparece en el
financiero y no en el registro vigente. La llave de cruce es el NIT, como estaba previsto.


## 3. Proxy de tamaño

**`numero_empleados` está vacío en las 57,663 filas y así queda.** Ninguna fuente
pública colombiana lo expone por institución: no está en REPS, ni en los archivos de
Supersalud, ni en RUES, ni PILA se publica a nivel de entidad. No se estimó.

Lo que sí hay, por sede y con cantidad:

| Grupo | Registros | Unidades | Sedes |
|---|---|---|---|
| CONSULTORIOS | 63,528 | 136,139 | 61,981 |
| CAMAS | 6,631 | 100,130 | 2,099 |
| CAMILLAS | 4,817 | 23,411 | 1,710 |
| SALAS | 13,294 | 19,497 | 11,308 |
| SILLAS | 948 | 18,732 | 758 |
| AMBULANCIAS | 7,627 | 7,627 | 2,436 |
| UNIDAD MOVIL | 704 | 704 | 597 |

Camas totales del país en el registro: **100,130**.

### Conceptos con más unidades

| Grupo | Concepto | Unidades |
|---|---|---|
| CONSULTORIOS | Consulta Externa | 132,064 |
| CAMAS | Adultos | 49,142 |
| SALAS | Procedimientos | 14,720 |
| CAMAS | Pediátrica | 9,270 |
| CAMILLAS | Observación Adultos Mujeres | 8,947 |
| CAMILLAS | Observación Adultos Hombres | 8,497 |
| CAMAS | Salud Mental Adulto | 7,832 |
| CAMAS | Intensiva Adultos | 6,647 |
| SILLAS | Sillas de Hemodiálisis | 6,112 |
| AMBULANCIAS | Básica | 5,999 |
| CAMILLAS | Observación Pediátrica | 5,305 |
| CONSULTORIOS | Urgencias | 4,075 |
| CAMAS | SPA Básico Adultos | 3,692 |
| CAMAS | Paciente crónico sin ventilador | 3,640 |
| CAMAS | Intermedia Adultos | 3,509 |

### Cobertura del proxy

| Grupo | Entidades | Con capacidad declarada |
|---|---|---|
| resto | 48,691 | 44,289 |
| IPS | 8,972 | 7,920 |


## 4. Deduplicación

La llave de identidad es **(tipo_identificacion, numero_identificacion)**, no el NIT solo:
con el universo completo entran decenas de miles de profesionales independientes
identificados con cédula, y un número de cédula puede coincidir con un NIT.

- Entidades únicas: **57,663**
- Códigos de habilitación: **61,177**
- Entidades con más de un código fusionadas: **2,119** (registradas en `qa_dedup` con los códigos, las razones sociales vistas y el criterio)

Criterio de razón social canónica: la del registro con `fecha_radicacion` más reciente;
a igualdad de fecha, la cadena más larga.

### Las diez entidades con más códigos de habilitación

| NIT | Razón social | Códigos | Sedes |
|---|---|---|---|
| 900777063 | Sporty City SAS | 28 | 225 |
| 800087565 | SYNLAB COLOMBIA S.A.S. | 25 | 90 |
| 860013779 | ASOCIACIÓN PROFAMILIA | 24 | 46 |
| 901041691 | CENTROS MEDICOS COLSANITAS S.A.S | 24 | 104 |
| 800149384 | CLÍNICA COLSANITAS S.A. | 22 | 104 |
| 900532504 | DAVITA S.A.S. | 22 | 40 |
| 900123436 | SOCIEDAD INTEGRAL DE ESPECIALISTAS EN SALUD  | 21 | 25 |
| 800003765 | VIRREY SOLIS IPS SA | 21 | 106 |
| 830007355 | DAVITA COLOMBIA SAS | 20 | 39 |
| 830015870 | SISMEDICA SOCIEDAD POR ACCIONES SIMPLIFICADA | 19 | 20 |


## 5. Calidad e inconsistencias

| Tipo | Casos |
|---|---|
| dv_invalido | 1,136 |
| sin_match_reps | 1,013 |
| servicio_sin_sede | 130 |
| capacidad_sin_sede | 64 |
| fila_malformada | 45 |
| sede_sin_registro | 2 |

Lectura de cada tipo:

- `sin_match_reps` — NIT que reportó a Supersalud en 2021 y no está en el REPS vigente.
- `dv_invalido` — NIT cuyo dígito de verificación no valida con el algoritmo DIAN. Se carga igual; se marca.
- `nit_mal_formado` — identificación con caracteres no numéricos.
- `fila_malformada` — fila cuyo número de campos no coincide con la cabecera, por separador embebido en texto libre. El portal no escapa los delimitadores.
- `servicio_sin_sede` / `capacidad_sin_sede` / `sede_sin_registro` — referencias a sedes ausentes del export correspondiente. Los cuatro exports se tomaron con minutos de diferencia y el registro se mueve; son deriva de snapshot, no error de modelo.
- `sede_duplicada` — par (código de sede, número de sede) repetido.
- `tipo_documento_derivado` — no es una incidencia cargada sino un conteo del resumen: 3,968 registros cuyo tipo de documento no estaba en el dataset abierto y se dedujo de `clase_persona`.

## 6. Desfase entre el dataset abierto y la consulta web

| | Dataset abierto `c36g-9fc2` | Consulta web en vivo |
|---|---|---|
| Corte declarado por la fuente | `Fecha corte REPS: Mar 12 2026  3:11PM` | `Sep 16 2026 12:25AM` |
| Última actualización del portal | 2026-04-17 | — |
| Filas de sedes | 76,821 | 77,009 |
| Códigos de prestador distintos | 61,073 | 61,177 |
| Servicios habilitados | **no expone el campo** | 228,039 |
| Capacidad instalada | **no expone el campo** | 97,549 |
| Representante legal | **no expone el campo** | 17,779 |

**Diferencia de cobertura entre las dos fuentes:**

- 3,968 códigos de prestador están en la consulta web y **no** en el dataset abierto (altas posteriores al 12-mar-2026).
- 3,864 códigos están en el dataset abierto y **no** en la consulta web (bajas, cierres o pérdida de habilitación desde entonces).
- Movimiento neto del registro en ~6 meses: +104 prestadores.

Se cargó la consulta web. El dataset abierto se usó solo para recuperar
`tipo_identificacion`, que el export web trae vacío.

## 7. Trazabilidad

Cada tabla lleva `extraccion_id`. La tabla `extraccion` registra por archivo: URL,
fecha de descarga, corte declarado por la fuente, sha256, bytes, filas y separador.

| Fuente | Recurso | Corte declarado | Bytes | Filas | sha256 |
|---|---|---|---|---|---|
| reps | prestadores | Sep 16 2026 12:25AM | 20,842,564 | 61,177 | 80d9d634e543 |
| reps | sedes | Sep 16 2026 12:26AM | 31,491,539 | 77,011 | 5b31d16bdeac |
| reps | servicios | Sep 16 2026 12:26AM | 157,237,235 | 228,169 | 2c0501b69cfe |
| reps | capacidad | Sep 16 2026 12:27AM | 33,641,225 | 97,613 | ade15d5b054f |
| supersalud | privadas_2021 | 31-dic-2021 | 8,510,044 |  | 7b21201347e8 |
| supersalud | publicas_2021 | 31-dic-2021 | 1,045,319 |  | 4a32ee18977a |

## 8. Limitaciones

1. **Supersalud va cinco años atrasado.** La última vigencia publicada es 2021. No es una limitación del pipeline: la entidad no ha publicado cortes posteriores pese a que la obligación de reporte por Circular Única sigue vigente.
2. **No hay número de empleados en ninguna fuente pública.** El campo queda NULL. El sustituto es la capacidad instalada.
3. **El `Registro Actual` del REPS solo trae prestadores habilitados.** Los cerrados o con habilitación vencida no aparecen; por eso una parte del financiero de 2021 no cruza.
4. **El dataset abierto de datos.gov.co no sirve como fuente primaria**: corte de marzo de 2026 y sin servicios, capacidad, representante legal ni fechas de habilitación.
5. **El portal no escapa los delimitadores** en campos de texto libre. Las filas afectadas están contadas y registradas una por una.
6. **`tido_codigo` viene vacío** en el export web; el tipo de documento se recuperó del dataset abierto y, donde no cruzó, se derivó de `clase_persona`.
7. **SIHO (Decreto 2193)** tiene planta de personal de las ESE, pero su módulo público de invitado no expone esa consulta. Quedó sin verificar como vía de descarga.
