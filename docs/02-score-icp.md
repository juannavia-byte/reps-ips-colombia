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

## La auditoría del LTV (v3)

Se revisó el otro lado de la ecuación y aparecieron dos cosas.

**1. Una variable estaba contada dos veces.** `contratistas` se calculaba como
fuerza laboral menos planta, pero la fuerza laboral es `planta × 5,24` con un
factor fijo. Entonces `contratistas = planta × 4,24`: **la correlación entre
ambas era 1,0000 exacta**. Eran los dos pesos más altos del LTV, así que el dato
menos confiable del modelo pesaba la mitad del valor total. Eliminada.

**2. Faltaba la variable dura más predictiva.** Se ajustó una regresión de
`ln(ingresos)` sobre los **4.650 prestadores que reportaron a Supersalud**, y
`sedes` salió con la elasticidad más alta de todo el dato duro: **1,12**. Hasta
ahora `sedes` solo existía en el CAC, como fricción — el modelo cobraba el costo
de tener una red sin acreditarle el valor que esa red representa.

El dato duro del REPS, sin la planta estimada, ya explica el **48,6 %** de la
varianza de los ingresos reales.

### Qué empuja el score hacia arriba

**Del lado del valor** — un término por tramo de la escalera, ocho de los nueve
son dato duro. El orden de los pesos sale de la regresión.

| Variable | Peso | Cobertura | Por qué |
|---|---|---|---|
| Planta en nómina → ARL | 0,55 | 100 % | Prima del ramo de entrada. **Dato estimado**: se le da menos peso del que la estadística le daría (la regresión le asigna 0,94), porque además su correlación con ingresos es en parte circular — la planta se deriva de la nómina, que sale del mismo estado de resultados. |
| **Sedes** | 0,45 | 100 % | **El predictor duro más fuerte.** Cada sede es un punto que asegurar. También está en el CAC: ahí cuesta, aquí vale. |
| Salas de cirugía → RC médica | 0,35 | 10 % | Donde se opera está la severidad. |
| Servicios de alta complejidad | 0,35 | 5 % | Segunda elasticidad más alta. |
| Amplitud de la escalera | 0,25 | 96 % | Grupos de servicio distintos: de cuántos tramos hay materia prima. No es lo mismo que el número de servicios. |
| Servicios habilitados | 0,20 | 96 % | Superficie total de exposición. |
| Camas → todo riesgo | 0,15 | 15 % | Hospitalización. |
| Ambulancias → autos | 0,12 | 23 % | Único proxy de flota propia. |
| Consultorios → RC contratistas | 0,10 | 82 % | Puestos de profesionales, muchos contratistas. Es el proxy que antes se pretendía medir con `contratistas`. |
| Crecimiento 19→21 | 0,30 | 34 % | Mira adelante; no estaba en la regresión. |

Con estos pesos, **el dato duro del REPS aporta el 77 %** del tamaño del LTV.

> Un cero en capacidad **no es dato faltante**: en el REPS la capacidad se declara,
> así que cero camas significa que de verdad no hospitaliza.

**Validación externa:** el LTV resultante correlaciona **0,718 con los ingresos
reportados** de los 4.650 que sí reportaron — tan bien como una regresión ajustada,
pero construido sobre dato duro y sin depender de la planta.

**Del lado del esfuerzo** (bajan el score)

| Variable | Peso | Por qué |
|---|---|---|
| Interlocutores | 1,0 | Se estima de sedes y tamaño, en log2. |
| Dispersión geográfica | 0,9 | Departamentos: desplazamiento y distancia a quien decide. |
| Duración del ciclo | 0,6 | Crece con los municipios. |
| Datos de contacto que faltan | 0,5 | Investigación previa por cada dato ausente. |

### Cómo se combinan: el exponente del esfuerzo

    cociente = LTV / CAC ^ exponente_cac        (hoy 1,8)

Con el cociente clásico (exponente 1) el CAC correlacionaba **+0,05** con el
resultado: **el esfuerzo subía levemente el score**, al revés de lo que debe ser.
Pasa porque valor y esfuerzo crecen juntos (correlación 0,37) y el LTV varía 3,6
veces más en logaritmo. Con 1,8 la correlación es **−0,18**: el esfuerzo resta sin
dominar.

El argumento de fondo no es estadístico. **La capacidad del equipo es el cuello de
botella real** —cap. 3.4 del método: 12 cuentas A por ejecutivo— y cuando la
restricción es capacidad, el esfuerzo debe penalizarse más que proporcionalmente.

Medido moviendo ese número: con exponente 1, Viva 1A queda en el puesto 195 del
universo; con 3 se hunde al 8.090. En 1,8 queda alrededor del 600, que es donde
tiene sentido una cuenta valiosa pero cara de trabajar.

| Cuenta | Sedes | LTV | CAC | Score |
|---|---|---|---|---|
| OINSAMED | 1 | 2,91 | 6,9 | **86,1** |
| Bonnadona | 2 | 3,18 | 9,4 | **79,8** |
| Clínica General del Norte | 29 | 4,21 | 14,1 | **73,9** |
| Clínica General San Diego | 1 | 0,61 | 6,2 | **67,9** |
| Viva 1A | 89 | 3,31 | 18,1 | **64,9** |

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
