# Score de ICP — cómo se usa y cómo se ajusta

Guía de operación. No hace falta leer el código.

## Qué es el número

Un cociente **LTV : CAC** por prestador, donde:

- **CAC** es **esfuerzo y tiempo**, no plata: cuánta gente hay que convencer, cuántos
  meses toma, cuánta investigación previa falta y si la decisión está fuera de la
  sede local.
- **LTV** es el valor de la **cuenta completa a 24 meses** —toda la escalera de
  cross-sell—, no solo la ARL de entrada.

El score que se ve es el **percentil** de ese cociente dentro del universo. Un score
de 93 significa: *esta cuenta está en el 7 % mejor*. Por eso no hay empates arriba y
no hay que recalibrar nada cuando se mueve un peso.

| Prioridad | Qué es | Cuántas hay hoy |
|---|---|---|
| **alta** | percentil ≥ 90 | 1.066 |
| **media** | percentil ≥ 65 | 2.647 |
| **baja** | el resto | 6.933 |

## ⚠️ Los pesos de hoy son un punto de partida

No hay historial de cierres en HubSpot con el cual calibrarlos. **Salieron de la
lógica del método comercial, no de datos.** Sirven para ordenar desde el primer día,
y se corrigen con cierres reales (ver abajo). No los presentes como un modelo
validado, porque no lo es todavía.

Lo único calibrado contra datos es el **reparto** (10 / 25 / 65 %), que se fijó
mirando la distribución real del universo el 16-sep-2026.

## Qué empuja el score hacia arriba

**Del lado del valor**

| Variable | Peso inicial | Por qué |
|---|---|---|
| Contratistas tercerizados | **1,3** | El más alto a propósito. Es la brecha entre fuerza laboral y nómina propia: el volumen de médicos contratistas que habilita el ancla de RC de contratistas, que es lo que de verdad retiene la cuenta. |
| Planta en nómina | 1,0 | Base de la prima de ARL, el ramo de entrada. |
| Crecimiento 19→21 | 0,7 | Una IPS que crece tiene más recorrido de cross-sell. |
| Señal de reclasificación | ×1,35 | Habilitó cirugía, UCI, oncológico, quemados, salud mental o trasplantes, y con alta probabilidad sigue cotizando ARL en la clase III inicial. |
| Servicio nuevo en 12 meses | ×1,20 | Disparador público y fechado. |

**Del lado del esfuerzo** (bajan el score)

| Variable | Peso inicial | Por qué |
|---|---|---|
| Decide un corporativo lejos | **1,2** | La fricción más cara: no se resuelve con más visitas. |
| Interlocutores | 1,0 | Lo que más alarga un ciclo. |
| Duración del ciclo | 0,6 | Pesa menos porque el tiempo se paralelice entre cuentas; convencer gente no. |
| Datos de contacto que faltan | 0,5 | Investigación previa por cada dato ausente. |

**Correcciones**

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

### Si al mover pesos todo queda igual

El score es un percentil: lo que importa es el **orden**, no el nivel. Si subes
todos los pesos a la vez, nada cambia — es correcto. Para mover el ranking hay que
cambiar la **proporción** entre variables.

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
