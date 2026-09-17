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
