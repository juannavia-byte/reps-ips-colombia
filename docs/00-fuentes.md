# Fuentes verificadas

Todo lo de abajo se comprobó entrando a la fuente, no leyendo su documentación.
Fecha de verificación: 15–16 de septiembre de 2026.

## 1. REPS · dataset abierto (datos.gov.co)

**`c36g-9fc2`** — *Registro Especial de Prestadores y Sedes de Servicios de Salud*, MinSalud.

- API Socrata/SODA: `https://www.datos.gov.co/resource/c36g-9fc2.json` (JSON, CSV, XLSX;
  soporta `$select/$where/$group/$limit/$offset`).
- 76.821 filas, una por sede. 22 columnas. 61.073 códigos de prestador.
- `rowsUpdatedAt` = 17-abr-2026, pero el dato interno declara
  **`Fecha corte REPS: Mar 12 2026`**.
- **Trae:** código de prestador, nombre, NIT, tipo de identificación, naturaleza
  jurídica, ESE sí/no, departamento y municipio (prestador y sede), dirección,
  email, teléfono, clase de prestador.
- **No trae:** servicios habilitados · capacidad instalada · representante legal ·
  fecha de habilitación y vencimiento · nivel de atención · carácter territorial ·
  horarios · barrio · zona.

**Conclusión: no sirve como fuente primaria.** Se usa solo para recuperar
`tipo_identificacion` (la consulta web lo trae vacío) y para medir el desfase.

## 2. REPS · consulta pública en vivo — FUENTE PRIMARIA

`https://prestadores.minsalud.gov.co/habilitacion/` · ASP.NET WebForms con
frameset · login público `invitado` / `invitado`.

Fecha de corte observada: **`Sep 16 2026 12:25AM`**, es decir, del día. El dataset
abierto va **seis meses atrasado** frente a esto.

Cinco páginas de consulta, cada una con exportación a texto delimitado:

| Página | Archivo | Columnas | Filas nacionales |
|---|---|---|---|
| `consultas/habilitados_reps.aspx` | `Prestadores.csv` | 36 | 61.177 |
| `consultas/sedes_reps.aspx` | `Sedes.csv` | 47 | 77.011 |
| `consultas/serviciossedes_reps.aspx` | `Servicios.csv` | 94 | 228.169 |
| `consultas/capacidadesinstaladas_reps.aspx` | `CapacidadInstalada.csv` | 31 | 97.613 |
| `consultas/medidasseguridad_reps.aspx`, `consultas/sanciones_reps.aspx` | — | — | no cargadas |

**Buscar sin filtros devuelve el universo nacional en una sola respuesta, sin
paginación.** Los cuatro exports juntos pesan 243 MB y tardan ~2,5 minutos.

### Trampas del portal (resueltas en `src/reps_client.py`)

1. Las seis "pestañas" **no son paneles**: son páginas `.aspx` distintas. Hacer
   postback del botón de pestaña sobre `habilitados_reps.aspx` y luego buscar
   devuelve el grid de PRESTADORES, no el de la pestaña.
2. El querystring `pageTitle` debe ir con `+`, no con `%20`, o `Page_Load` lanza
   `FormatException`.
3. En el postback hay que enviar **solo los `<input type=hidden>`**. Serializar el
   formulario completo rompe la validación de eventos de ASP.NET porque el
   `<select>` de municipio se renderiza sin opciones.
4. El campo `tbSeparator` **se respeta en `habilitados_reps.aspx` y se ignora en
   las otras tres**, que siempre salen con `;`. El loader detecta el separador
   por archivo.
5. El portal **no escapa los delimitadores** en campos de texto libre. 45 filas de
   403.970 quedan malformadas; están contadas y registradas una a una.
6. `tido_codigo` viene **vacío en las 61.177 filas**.

### Capacidad instalada — el proxy de tamaño

`grupo_capacidad` → `coca_nombre` → `cantidad`, por sede:

```
CAMAS         Adultos · Intensiva/Intermedia Adultos · Pediátrica · Neonatal ·
              Salud Mental · Paciente crónico con/sin ventilador · SPA · TPR
SALAS         Sala de Cirugía · Procedimientos · Partos
CONSULTORIOS  Consulta Externa · Urgencias
CAMILLAS      Observación Adultos H/M · Observación Pediátrica
SILLAS        Quimioterapia · Hemodiálisis · Ambiente de transición
AMBULANCIAS   Básica · Medicalizada (con placa, modelo y tarjeta)
UNIDAD MOVIL
```

## 3. Supersalud · estados financieros

**No hay información financiera en datos.gov.co.** Supersalud tiene ahí solo tres
datasets, todos de transparencia administrativa. El `4xqs-ec37` que aparece en
buscadores como *"Catálogo de información financiera de las IPS privadas"*
**ya no existe**: la API responde `dataset.missing`.

Lo que sí publica está en
`https://www.supersalud.gov.co/es-co/Paginas/Delegada Supervisión Institucional/Estadísticas-Financieras-IPS.aspx`
como Excel anuales servidos desde `docs.supersalud.gov.co`.

| Archivo | Contenido | Llave |
|---|---|---|
| `Catalogo_Cuentas_Diciembre_2021_IPS.xlsx` | IPS privadas por grupo NIIF: FT001-01 (125 IPS/390 cols), FT001-02 (4.097/351), FT001-03 (1.493/266) | NIT + razón social |
| `EEFF_IPS_Públicas_2021.xlsx` | 919 ESE, 167 cols. Fuente declarada: SIHO | NIT **y** código de habilitación |

Vigencias disponibles: 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, **2021**.

> **La última vigencia publicada es 2021.** No hay cortes posteriores, pese a que
> la obligación de reporte por Circular Única sigue vigente. Es una limitación de
> la fuente, no del pipeline.

Los tres grupos NIIF tienen planes de cuentas distintos, por eso el detalle va a
`jsonb` y solo se promueven a columnas activos, pasivos, patrimonio, ingresos y costos.

**Tasa de cruce contra el REPS por NIT:** 84,0 % grupo 1 · 84,0 % grupo 2 ·
77,6 % grupo 3 · **99,7 % ESE**. Global 84,7 %.

## 4. Número de empleados — no existe

| Candidato | Resultado |
|---|---|
| REPS | ❌ empleados · ✅ capacidad instalada con cantidad |
| SIHO (Decreto 2193) | ⚠️ Tiene planta de personal de ESE, pero el módulo público de invitado solo expone "Reporte Cumplimiento 2025". **Sin verificar como descargable** |
| PILA | ❌ No público a nivel de entidad |
| RUES | ⚠️ `ruesapi.rues.org.co` responde 404; `consultas.rues.org.co` no respondió. No publica empleados como regla |
| Supersalud | ❌ Ningún campo de planta ni nómina en los archivos 2021 |

**`prestador.numero_empleados` queda NULL en las 57.663 filas. No se estimó.**

## 5. Otras fuentes revisadas

- **Directorio de IPS** — `prestadores.minsalud.gov.co/directorio/consultaIPS.aspx`.
  Existe como consulta separada con exportación propia. Más pobre que el REPS,
  pero trae **sigla** y **URL del sitio web**, que el REPS no tiene. No cargado;
  queda como enriquecimiento posible.
- **Datasets departamentales y municipales en datos.gov.co** (Atlántico, Antioquia,
  Bolívar, Casanare, Cundinamarca, Barranquilla…). Son recortes territoriales
  publicados por entidades locales, con cortes viejos y esquemas heterogéneos.
  No aportan sobre la consulta nacional.
- **Agregados nacionales** `kjjp-kasm` (prestadores por departamento y clase) y
  `fa2g-cdft` (ambulancias, camas y salas por departamento). Son agregados, no
  registros por IPS.
