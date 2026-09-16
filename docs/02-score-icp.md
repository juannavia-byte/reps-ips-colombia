# Score de ICP — cómo se usa y cómo se ajusta

Guía de operación. No hace falta leer el código.

## Qué es el número

Un cociente **LTV : CAC** por prestador, donde:

- **CAC** es **esfuerzo y tiempo**, no plata: cuánta gente hay que convencer, cuántos
  meses toma, cuánta investigación previa falta y si la decisión está fuera de la
  sede local.
- **LTV** es el valor de la **cuenta completa a 24 meses** —toda la escalera de
  cross-sell—, no solo la ARL de entrada.

El score se obtiene estandarizando el **logaritmo** del cociente: resultó casi
normal (media −3,614, mediana −3,59, desviación 0,823 sobre 10.646 filas).

> **50 es el promedio del universo. Cada 10 puntos es una desviación estándar.**

| Prioridad | Qué es | Cuántas hay hoy |
|---|---|---|
| **alta** | ≥ 63 · más de 1,3 desviaciones sobre el promedio | 783 |
| **media** | ≥ 54 | 1.984 |
| **baja** | el resto | 7.879 |

Rango observado: 1,2 a 88,4. **Nada se pega al techo ni al piso**, así que el orden
tiene resolución en todo el recorrido.

## ⚠️ Los pesos de hoy son un punto de partida

No hay historial de cierres en HubSpot con el cual calibrarlos. **Salieron de la
lógica del método comercial, no de datos.** Sirven para ordenar desde el primer día,
y se corrigen con cierres reales (ver abajo). No los presentes como un modelo
validado, porque no lo es todavía.

Lo único calibrado contra datos es la **escala**: `centro`, `dispersion` y
`puntos_por_sigma` salieron de medir la distribución real del cociente el
16-sep-2026. El reparto que producen hoy es 7 % alta · 19 % media · 74 % baja.

## Por qué el CAC se rehizo (v2)

La primera versión estimaba el esfuerzo con una tabla de cuatro tramos de planta.
Medido sobre las 10.646 filas, el resultado fue que **el CAC tomaba solo 13 valores
y el 84,4 % de las empresas compartía exactamente el mismo (2,60)**. El cociente
correlacionaba 0,94 con el LTV y 0,26 con el CAC: el esfuerzo no estaba pesando.

La causa: dentro del tramo «menos de 50 personas» —9.737 empresas, el 91 % del
universo— **las sedes van de 1 a 48 y los municipios de 1 a 20**. La planta sola no
describe la estructura de una organización. Hay 111 prestadores de más de 100
personas con una sola sede y 116 con más de diez: perfiles opuestos que recibían
idéntico trato.

Ahora el esfuerzo se estima desde la **estructura**, con datos duros del REPS:

```
interlocutores = 1 + k_sedes·log2(1+sedes) + k_tamaño·log2(1+planta/ancla)
ciclo          = base + c_dispersión·log2(1+municipios) + c_tamaño·log2(1+planta/ancla)
dispersión     = log2(1+departamentos)
```

`log2` por dos razones: pasar de 1 a 4 sedes cambia la venta mucho más que pasar de
40 a 43, y **absorbe el ruido de los datos** — que la planta esté mal por un factor
de 2 mueve el término en 1 unidad, no en 100.

Resultado: el CAC pasó de **13 valores a 682**, con rango 4,9 a 21,4.

### La segunda corrección: la escala

Con el CAC arreglado, el score seguía saturando. El percentil se calculaba contra un
universo donde el 91 % son micro-prestadores que nunca se van a trabajar, así que
**cualquier IPS de más de 100 personas caía automáticamente sobre el percentil 98**:
dentro de la lista de trabajo todas salían con 98,7–98,9 sin importar su estructura.

Por eso el score dejó de ser percentil y pasó a ser el logaritmo del cociente
estandarizado. En una lista filtrada típica (291 IPS de la Costa) los scores ahora
se reparten entre 59 y 87 con 29 valores distintos, en vez de amontonarse en 98.

## El LTV: comisión esperada de la escalera de ramos (v4)

El LTV dejó de ser «lo que predice los ingresos de la IPS» y pasó a ser **la
comisión esperada de todos los ramos que le podemos colocar en 24 meses**.

Por cada ramo:

    aporte = valor_relativo × probabilidad_24m × norm(exposición)

- `valor_relativo` — comisión anual esperada, en escala relativa 0–100 entre ramos
- `probabilidad_24m` — P(el ramo está activo a 24 meses **dado que ganamos la cuenta**)
- `exposición` — suma ponderada de variables medibles del REPS

El total se parte en dos bloques con peso global propio: **corporativo 1,00 ·
personas 0,35**. Ese par de números es la palanca para inclinar la balanza sin
tocar ramo por ramo.

### ¿Hay conflicto entre «predecir ingresos» y «oportunidad de venta»?

Conceptualmente sí, y en dos casos concretos:

- Una **unidad renal o un centro de dispensación** factura mucho y expone poco:
  ingreso alto, prima baja.
- Una **clínica con quirófanos y hospitalización**, estrangulada por la cartera de
  las EPS, factura poco para lo que expone: ingreso medio, prima alta.

La regresión castigaba a la segunda, que es el mejor cliente para un corredor.
**Por eso manda la lógica de prima.**

Pero medido, el conflicto resultó **mucho menor de lo esperado**: el LTV por prima
correlaciona **0,737** con los ingresos reportados — incluso algo mejor que el
0,718 del modelo basado en regresión. Exposición y actividad económica van juntas,
así que reordenar por prima no pelea con los datos, los reordena dentro de la misma
estructura.

**La regresión no se descarta: queda como auditoría.** Al correr `score_icp.py` se
reporta esa correlación. Si cae por debajo de 0,45, es señal de que el modelo se
fue a la teoría y dejó de describir actividad real.

### Catálogo completo de seguros corporativos

Ordenado por comisión anual esperada. La columna «exposición» dice de qué variable
del REPS sale el tamaño.

| Ramo corporativo | Valor | Prob. 24m | Exposición medida |
|---|---|---|---|
| RC profesional médica institucional | 100 | 0,35 | salas de cirugía, camas, alta complejidad, internación |
| Todo riesgo daños materiales *(incluye terremoto)* | 85 | 0,25 | sedes, camas, salas, consultorios |
| Cumplimiento y garantía única | 55 | 0,45 | naturaleza pública, sedes, internación |
| Equipo biomédico y electrónico | 45 | 0,22 | **imagenología**, salas, camas UCI, alto costo |
| **RC contratistas** *(el ancla)* | 40 | **0,80** | consultorios, servicios |
| RC extracontractual (predios y labores) | 30 | 0,35 | sedes, urgencias, consultorios |
| Automóviles / flota | 30 | 0,15 | ambulancias |
| Manejo global e infidelidad | 25 | 0,30 | sedes, farmacia, alto costo |
| Lucro cesante por interrupción | 25 | 0,12 | camas, salas, internación |
| RC profesional individual (por médico) | 25 | 0,40 | consultorios, servicios |
| Ciberseguro y protección de datos | 20 | 0,20 | servicios, sedes, imagenología |
| Transporte de mercancías (cadena de frío) | 15 | 0,15 | laboratorio, farmacia, alto costo |
| RC ambiental (RESPEL) | 15 | 0,40 | cirugía, laboratorio, alto costo, internación |
| Sustracción y hurto calificado | 12 | 0,12 | farmacia, alto costo, sedes |
| RC patronal | 12 | 0,45 | planta |
| Rotura de maquinaria | 10 | 0,10 | internación, camas, salas |
| D&O (directores y administradores) | 10 | 0,08 | planta, sedes, naturaleza pública |
| Montaje y obras civiles | 8 | 0,06 | **servicios nuevos en 12 meses** |
| SOAT | 5 | 0,10 | ambulancias |

**Ramos que existen pero no entraron, y por qué:**

- **Seguro de crédito (cartera)** — Solunion no suscribe cartera de EPS ni de sector
  público, que es casi toda la facturación de una IPS. No aplica.
- **Fianzas y seriedad de oferta** — sin dato para dimensionarlas.
- **Multirriesgo PYME** — es un empaquetado de los anteriores, no una línea aparte.
- **RC productos** — quedó absorbido en `manejo_infidelidad` vía farmacia; sepárelo
  si llega a pesar.

### Catálogo de seguros de personal

| Ramo de personas | Valor | Prob. 24m | Exposición |
|---|---|---|---|
| ARL *(producto de entrada)* | 60 | **0,85** | planta |
| Vida grupo empresarial | 25 | 0,30 | planta |
| Salud colectiva / plan complementario | 20 | 0,20 | planta |
| Accidentes personales colectivo | 12 | 0,20 | planta |
| Voluntarios por descuento de nómina | 10 | 0,15 | planta, sedes |
| Exequias colectivo | 8 | 0,25 | planta, sedes |

> Los dos últimos suman **sedes** además de planta: necesitan jornadas presenciales,
> y el canal de jornadas ya está montado.

### ⚠️ Los valores de prima son estimaciones, no sus tarifas

`valor_relativo` y `probabilidad_24m` salieron de la lógica del método y de
magnitudes de mercado, **no de los libros de Proactivos**. Reemplazar
`valor_relativo` por la comisión anual esperada real de cada ramo —que ustedes sí
conocen— es **el ajuste que más mejoraría el modelo**, más que cualquier cambio
estadístico.

### Ejemplo: de dónde sale el valor de una cuenta

Bonnadona, desglosada por ramo:

| Ramo | Aporte |
|---|---|
| ARL | 51,0 |
| RC médica institucional | 35,0 |
| Todo riesgo daños | 21,3 |
| RC contratistas | 19,1 |
| Cumplimiento | 11,9 |
| Equipo biomédico | 9,9 |

Eso es lo que hace el modelo explicable en una reunión: no es «score 76», es
«tiene dos salas de cirugía y 295 camas, así que la RC médica y el todo riesgo
valen esto».

### Un ajuste en el CAC que salió de este cambio

Al entrar `sedes` al LTV como valor, quedó pesando también como fricción en el CAC,
y valor y esfuerzo empezaron a pelearse al dividir (su correlación subió a 0,54).
El argumento comercial apuntaba igual: **negociar con una red de 29 sedes en una
sola ciudad es una sola negociación.** Lo que cuesta no es el número de sedes sino
la dispersión —municipios y departamentos— y el gobierno corporativo.

`k_sedes` bajó de 0,9 a 0,35, y `exponente_cac` quedó en **2,2**, que es donde la
correlación entre el esfuerzo y el resultado es ≈ 0: **el esfuerzo queda
exactamente pagado**, ni premiado ni castigado de más. Subirlo por encima de 2,2 es
una decisión de negocio —preferir victorias rápidas cuando la capacidad aprieta—,
no una corrección estadística.

| Cuenta | Sedes | LTV | CAC | Score |
|---|---|---|---|---|
| OINSAMED | 1 | 232 | 6,3 | **84,9** |
| Bonnadona | 2 | 230 | 8,5 | **76,5** |
| Cl. General del Norte | 29 | 326 | 11,4 | **72,9** |
| Clínica General San Diego | 1 | 62 | 5,7 | **71,2** |
| Viva 1A | 89 | 345 | 14,5 | **67,0** |

**Correcciones****Correcciones**

- **Piso operativo**: por debajo de 100 personas el valor se multiplica por 0,35. El
  cap. 3.2 del método descarta esas cuentas por no pagar el costo de servirlas.
  Sin este piso el score premiaba IPS de 30 personas por ser fáciles de cerrar —
  que es justo el error que comete un cociente LTV:CAC sin suelo en el LTV.
- **ESE** (pública): multiplicador 0,15. No se eliminan de la base, se hunden en el
  ranking de frío porque se ganan por SECOP. Subir ese número las devuelve.
- **Deterioro financiero**: patrimonio negativo ×0,75, pérdida operacional ×0,9.
  Reordena hacia abajo, **no descarta**.

## Ajustar los pesos

### Para probar (no queda guardado)

En el tablero, panel **«Pesos del score ICP»** en la barra lateral. Mueve un slider y
la tabla se reordena al instante. Sirve para responder *«¿y si los contratistas
pesaran el doble?»* sin tocar nada. Al recargar la página vuelve a los pesos del
archivo. El botón **Volver a los pesos del archivo** deshace todo.

### Para dejarlo fijo

1. Editar `config/pesos_icp.json`. Cada peso trae su `_nota` explicando qué mueve.
2. Subir la `version` (queda registrada en cada fila, para saber con qué receta se
   calculó).
3. Regenerar:

```bash
DSN="postgresql://reps:reps@localhost:55432/reps"
python src/score_icp.py    --dsn "$DSN"
python src/build_tablero.py --dsn "$DSN"
./deploy_pages.sh
```

**No hay ni un peso escrito en el código.** Si al leer `src/score_icp.py` falta un
número, está en el JSON.

### Recalibrar la escala

`escala.centro` y `escala.dispersion` son constantes fijas, y por eso mover un peso
**sube o baja los scores de verdad** en vez de recentrarlo todo. Al correr
`score_icp.py` se imprimen los valores observados y avisa si se desfasaron más de
media desviación:

```
calibración ok · ln(cociente) observado: media -3.614, desviación 0.823
```

Si tras un cambio grande de pesos aparece el aviso, se corre una vez:

```bash
python src/score_icp.py --dsn "$DSN" --autocalibrar
```

y el propio script reescribe `escala.centro` y `escala.dispersion` en el JSON con
lo observado. **Cambiar `exponente_cac` o varios pesos a la vez siempre exige
recalibrar**, porque mueven la escala del cociente entero.

## Ver por qué una cuenta sacó ese score

Cada fila guarda su desglose:

```sql
SELECT p.razon_social, s.score, s.prioridad,
       jsonb_pretty(s.desglose)
FROM reps.score_icp s JOIN reps.prestador p ON p.id = s.prestador_id
WHERE p.numero_identificacion = '900219120';
```

El desglose trae el aporte de cada variable al LTV y al CAC, las señales de alto
riesgo que se dispararon, y si le pegó el piso operativo o el deterioro financiero.
En el CSV exportado van `score_icp`, `prioridad_icp`, `ltv_estimado` y `cac_esfuerzo`.

## Registrar un cierre y recalibrar

Cada vez que cierres una cuenta, una línea en `datos/cierres.csv`:

```
nit;razon_social;fecha_cierre;segmento_real;canal_entrada;ciclo_dias;interlocutores_reales;ramos_activados;hubo_reclasificacion;notas
900219120;VIVA 1A IPS SA;2026-03-10;ips_grande;referido;145;4;4;si;cuenta ancla
```

| Campo | Qué poner |
|---|---|
| `nit` | Sin dígito de verificación |
| `segmento_real` | `ips_grande` · `ips_mediana` · `ips_pequena` · `ese` · `otro` |
| `canal_entrada` | `referido` · `frio_llamada` · `frio_correo` · `linkedin` · `visita` · `secop` · `otro` |
| `ciclo_dias` | Del primer contacto a la firma |
| `interlocutores_reales` | Cuánta gente hubo que convencer |
| `ramos_activados` | Cuántos ramos quedaron activos |
| `hubo_reclasificacion` | `si` / `no` — si se movió la clase de riesgo de ARL |

Después:

```bash
python src/recalibrar.py --dsn "$DSN"
```

Compara lo que el modelo predijo contra lo que pasó y dice **qué peso parece mal
puesto y hacia dónde moverlo**. Ejemplo de salida real:

```
▸ cac.pesos.ciclo_meses
    El ciclo real va POR ENCIMA del estimado en 38 días de mediana (5 casos).
    → Subir el peso.
▸ el score completo
    3 de 5 cierres entraron por «referido», que el score no modela.
    → El score ordena outbound en frío. Si el negocio entra por referido, la lista
      de alta sirve para decidir A QUIÉN pedir el referido, no a quién llamar.
```

**No ajusta nada solo, a propósito.** Con una docena de cierres, un ajuste automático
produce pesos peores que los de partida y con apariencia de estar calibrados. Avisa
mientras haya menos de 12 casos.

## Dónde vive cada cosa

```
config/pesos_icp.json   los pesos y los umbrales · lo único que se edita a mano
src/score_icp.py        el cálculo -> tabla reps.score_icp con el desglose
tablero/index.html      espejo del cálculo en JavaScript, para los sliders
datos/cierres.csv       el registro de cierres reales
src/recalibrar.py       compara predicho contra real y señala pesos sospechosos
```

La fórmula está escrita dos veces: en Python para la base y el export, y en
JavaScript para que los sliders reordenen sin regenerar. Para que no se
desincronicen, el tablero compara al arrancar su propio cálculo con el que trae la
base y **avisa por consola si difieren en más de 1,5 puntos**. Si ves ese aviso,
alguien cambió una fórmula y no la otra.
