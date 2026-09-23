# Motor de enriquecimiento de contactos

Para cada prestador, quién trabaja ahí, con qué cargo y por dónde se le
escribe. Cada afirmación con su fuente, su fecha y su nivel de certeza.

## Por qué no hay herramientas de pago

Apollo, Hunter, Lusha, RocketReach y compañía **no tienen datos de IPS
colombianas**, ni de las más grandes del país. Se probaron. Su cobertura se
construye sobre LinkedIn y bases anglosajonas, y el tejido de prestadores
colombianos —clínicas regionales, ESE municipales, IPS familiares— no está
ahí.

Las cuatro fuentes que sí funcionan son colombianas, oficiales y gratuitas.

## Las fuentes

| fuente | qué da | dónde |
|---|---|---|
| **REPS** | representante legal, correo y teléfono de habilitación | ya en la base |
| **RUES** | representante legal, su cédula, estado de matrícula | datos.gov.co `c82u-588k` · 9,4 M registros |
| **SECOP proveedores** | correo y teléfono del representante legal, sitio web | datos.gov.co `qmzu-gj57` |
| **SECOP contratos** | ordenador del gasto, supervisor | datos.gov.co `jbjy-vk9h` |
| **Sitio corporativo** | el organigrama real: Tier 2, 3 y 4 | la web de cada empresa |

**REPS trae representante legal en el 99,9 %** de los prestadores. El Tier 1
no hay que salir a buscarlo: ya estaba.

**RUES confirma.** Sobre 12 cuentas de prioridad alta, 11 coincidieron con
REPS y 1 difirió. Esa coincidencia entre dos fuentes oficiales independientes
es la señal de confianza más fuerte que existe sin llamar por teléfono.

**SECOP contratos casi no sirve para el ICP.** Da ordenador del gasto en el
85,8 % de los contratos, pero sólo cuando el prestador es la entidad que
COMPRA con dinero público. Las cuentas de prioridad alta son IPS privadas: de
1.393, sólo 3 tenían alguien por esta vía, y 189 de esas 191 personas eran de
un único hospital.

**El sitio web es la única vía al Tier 2/3, y rinde poco.** De 789 dominios,
54 dieron equipo: **6,8 %**. Ciento cuarenta y tres no respondieron y 96
bloquean por `robots.txt`, que se respeta. La mayoría de clínicas publica
misión, visión y valores, no su organigrama. Donde acierta acierta fuerte —un
solo sitio dio nueve personas con cargo— pero no cierra el hueco: lo reduce.

## Las tablas

Tres, y no una, por una razón concreta: una sola tabla obliga a dar **una**
confianza y **una** fecha a datos que no valen lo mismo. El nombre puede venir
confirmado por dos fuentes oficiales y el correo ser una conjetura de patrón.
Juntos en la misma fila, o se miente sobre el correo o se infravalora el
nombre.

```
enriquecimiento.persona     quién es y qué cargo tiene
enriquecimiento.canal       cada vía de contacto, con SU confianza y estado
                            correo · telefono · whatsapp · web · linkedin
                            facebook · instagram · x · tiktok · telegram
enriquecimiento.evidencia   de dónde salió cada afirmación, con el registro crudo
enriquecimiento.exclusion   lista de no-contactar (habeas data)
enriquecimiento.corrida     una fila por ejecución, con coste
```

### Se guarda toda persona confirmada

No sólo los cuatro tiers. El tier es una **etiqueta para ordenar el esfuerzo**,
no un filtro de entrada: quien usa Ariad elige a quién contactar mirando la
lista completa, y un cargo que hoy parece irrelevante es la puerta de entrada
de mañana.

### Qué significa cada estado

| estado | qué quiere decir |
|---|---|
| `verificado` | dos fuentes independientes coinciden |
| `inferido` | una sola fuente, o generado por patrón |
| `obsoleto` | alguna fuente dice que ya no está (matrícula cancelada) |

Y en los canales, `ambito` distingue el buzón **nominal** de una persona del
**buzón de área** (`contratacion@`, `gerencia@`) y del dato **personal**. No es
decorativo: son tres cosas distintas en una secuencia de outreach, y la tercera
es la que más cuidado exige bajo la Ley 1581.

### Un correo nunca se marca verificado por tener MX

MX prueba que el dominio recibe correo, **no que el buzón exista**. Sondear el
buzón por SMTP sí lo probaría, pero hace que los servidores marquen la IP que
sondea, y ese precio lo paga el dominio desde el que después se escribe. Un
correo sólo pasa a `verificado` si dos fuentes independientes lo traen igual.

Los generados por patrón entran con confianza 25 y se cuentan aparte en el
informe de cobertura. La primera versión les daba 60 —la misma nota que a un
correo publicado en SECOP— y entonces «96 de cada 100 con correo nominal»
resultaba ser casi todo conjeturas.

## La captura desde Ariad, empresa por empresa

Es la vía principal. Se entra por **`/portal/ariad`** —con sesión— se busca la
empresa, se abre su ficha y arriba de «Quién decide» aparecen los cargos que
todavía no tiene cubiertos, con su tier. Se escribe el nombre en el que
corresponda, se despliegan los diez campos de contacto, se guarda.

**«Quién decide» es UNA lista.** Lo que encontró el motor y lo que capturó el
equipo se funden por clave de nombre —tokens sin tildes y ordenados, la misma
regla de la base— así que nadie sale dos veces aunque esté en los dos sitios.

### Una persona con dos cargos es una fila, no dos

En una IPS de municipio el representante legal y el gerente general son la
misma silla. Al escribir en un segundo hueco un nombre que ya está en la ficha,
el formulario lo dice —«es X, que ya está en la ficha como Y»— y al guardar le
**añade** el cargo en vez de crear otra persona: los cargos conviven en el
mismo campo separados por `·`, y `clasificar` los clasifica por el más alto.

### Corregir y quitar

Cada persona de la lista lleva **Editar** (o **Añadir contacto**, si la trajo el
motor y todavía no tiene fila de captura). Ahí se corrige el nombre, los cargos
y cada canal ya guardado; vaciar un canal con `×` lo **retira**.

Retirar no es borrar: la fila se queda con quién y cuándo lo quitó. Desde el
navegador nadie tiene `DELETE`, y es a propósito — un borrado de verdad es el
que pide alguien invocando habeas data, y pasa por la lista de exclusión, que
es donde queda constancia de la solicitud. Lo retirado que ya se había bajado
lo marca `obsoleto` en Ariad la siguiente corrida de `traer_captura.py`.

**Dos grafías del mismo ser humano** —«Yuly Martínez» y «Yuly Andrea
Martínez»— son dos personas para la clave, y tienen que serlo: desde la ficha
no hay forma de distinguir un segundo nombre de más de una hermana con el mismo
apellido. Se juntan editando una y poniéndole el nombre de la otra: al guardar
se funden, los canales se mudan y la que sobra queda retirada.

Lo capturado queda en `captura.persona` y `captura.canal` de Supabase, marcado
«sin bajar», y de ahí vuelve a la base local:

```bash
PYTHONPATH=src python src/traer_captura.py --dsn "$DSN" \
    --supabase "$SUPABASE_DSN" --simular
PYTHONPATH=src python src/traer_captura.py --dsn "$DSN" --supabase "$SUPABASE_DSN"
PYTHONPATH=src python src/build_tablero.py --dsn "$DSN"
python src/push_tablero.py --dsn "$DSN" --supabase "$SUPABASE_DSN"
```

Lo que se captura entra **verificado con 90**; si se desmarca «lo vi o me lo
dijeron», entra **inferido con 40** y la ficha lo rotula «sin confirmar».

**Los cargos sugeridos salen de la tabla `CARGOS` del motor**, no de una lista
escrita en el HTML: el que se sugiere es exactamente el que el clasificador
sabe reconocer, y con «otro cargo» se añade cualquiera que no esté.

### Montarlo la primera vez

```bash
psql "$SUPABASE_DSN" -v ON_ERROR_STOP=1 -f supabase/captura.sql
psql "$SUPABASE_DSN" -v ON_ERROR_STOP=1 -f supabase/captura_avance.sql
psql "$SUPABASE_DSN" -v ON_ERROR_STOP=1 -f supabase/captura_edicion.sql
```

Y en Supabase: **Settings → API → Exposed schemas**, añadir `captura`. Sin eso
PostgREST responde `PGRST106` y el formulario no guarda.

`captura_edicion.sql` es el que añade `retirado`. Sin aplicarlo, guardar
responde `PGRST204` y el formulario lo dice con esas palabras en vez de
escupir el error de PostgREST.

En `proactivos-website`, tras cualquier cambio del tablero:

```bash
npm run vendorizar:ariad     # regenera src/herramienta/plantilla.ts
```

## La captura a mano en hoja, empresa por empresa

Las fuentes automáticas dan el Tier 1 casi completo y poco más: el sitio web
rinde 6,8 % y SECOP no sirve para IPS privadas. El resto se investiga a mano, y
para eso hay una hoja con ruta de vuelta.

```bash
python src/hoja_captura.py --salida dist/captura.csv          # todo el pipeline
python src/hoja_captura.py --nit 900772387,891800330          # sólo esas dos

# ... se llena en Sheets o Excel ...

PYTHONPATH=src python src/importar_hoja.py --dsn "$DSN" \
    --entrada dist/captura.csv --simular
PYTHONPATH=src python src/importar_hoja.py --dsn "$DSN" --entrada dist/captura.csv
python src/build_tablero.py --dsn "$DSN"                      # y aparece en Ariad
```

Una fila por **persona**, con once columnas de canal: correo, celular, fijo,
WhatsApp, LinkedIn, Facebook, Instagram, X, TikTok y Telegram. Tres filas por
empresa; si una da más gente, se duplica una fila y se cambia el nombre — el
importador agrupa por NIT, no por posición.

**Es idempotente.** Se puede importar a medio llenar, seguir llenando y volver a
importar. Ni duplica ni hay que limpiar antes.

### Qué resuelve solo

| se escribe | queda |
|---|---|
| `@anaruiz`, `facebook.com/anaruiz`, la URL con `?utm_source=…` | el mismo canal, una sola vez |
| `gerencia@`, `contratacion@` | ámbito `area`, sin tocarlo |
| `300 555 12 34` en Celular, `604 444 5566` en Fijo | los dos `telefono`, distinto ámbito |
| `CLINICA DEL NORTE SAS` en la columna del nombre | rechazada como persona; sus canales quedan en la empresa |
| una fila sin nombre | canales a nivel de empresa, `persona_id` nulo |

### La columna `Confirmado` es la que decide la confianza

`si` (o vacío) entra **verificado con 90**; `no` entra **inferido con 40** y
Ariad lo rotula «sin confirmar». Teclear un dato no lo verifica — si todo lo
escrito a mano entrara como verificado, la palabra dejaría de significar algo
en la base entera.

### Redes: el CHECK no las dejaba entrar

`traer_hubspot.py` mapeaba `hs_facebookid` → `facebook` desde el primer día,
pero el CHECK de `canal.tipo` sólo aceptaba cinco valores y ninguno era ése:
todo contacto de HubSpot con Facebook o Instagram reventaba contra la
restricción. Nunca se notó porque nadie había llenado esos campos. Se corrige
en `src/migracion_canales_sociales.sql`, que hay que correr una vez sobre las
bases que ya existían.

## Uso

```bash
DSN="postgresql://reps:reps@localhost:55432/reps"

# Fuentes oficiales. Rápido: ~84 peticiones por cada 1.400 empresas.
python src/enriquecer.py --dsn "$DSN" --prioridad alta
python src/enriquecer.py --dsn "$DSN" --prioridad alta --simular   # sólo contar

# Sitios web. Lento a propósito: un sitio a la vez, con pausa.
PYTHONPATH=src python src/raspar_web.py --dsn "$DSN" --prioridad alta

# Exportar
python src/exportar_contactos.py --dsn "$DSN" --prioridad alta \
  --salida dist/contactos.csv
python src/exportar_contactos.py --dsn "$DSN" --prioridad alta \
  --tier-max 2 --sin-adivinados --formato hubspot --salida dist/hubspot.csv
```

Ambos scripts son **idempotentes**: volver a correrlos actualiza en vez de
duplicar, que es lo que hace posible la re-verificación periódica sin limpiar
antes.

## Cumplimiento (Ley 1581 de 2012)

El representante legal es dato público —está en RUES— y no hay restricción
para usarlo en contacto comercial B2B. Cada canal guarda su `base_licitud`
para que, si alguien pregunta por qué tenemos su dato, la respuesta esté en la
fila y no en la memoria de nadie.

La tabla `exclusion` es la lista de no-contactar. Se consulta **antes de
exportar** y antes de construir el payload del tablero: da igual lo que haya
en la base si lo que sale hacia una campaña ya está filtrado. Acepta NIT,
nombre o dato suelto, porque quien pide no ser contactado rara vez sabe con
qué identificador figura.

No se almacenan ni se infieren datos sensibles. El alcance es estrictamente
contacto laboral.

## Lo que este motor no hace

- **No ejecuta JavaScript.** Los sitios que pintan su equipo desde el navegador
  quedan fuera, y el informe lo dice en vez de disimularlo.
- **No entra donde `robots.txt` lo prohíbe.**
- **No busca en LinkedIn.** Raspar su sitio viola sus términos, y las APIs con
  licencia son justamente las herramientas que no cubren Colombia.
- **No inventa.** Un dato que no se encontró se queda vacío.
