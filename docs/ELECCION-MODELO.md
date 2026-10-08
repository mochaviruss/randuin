# Elección del modelo · Randuin (EP2)

**Decisión:** la red **64-32 sin peso de clase** (modelo 4 del notebook de Max), entrenada con TensorFlow y servida
en ONNX. Es la configuración por defecto de `servicio-ia/entrenar.py`.

## Punto de partida

El notebook `notebooks/randuin_modelo_tensorflow.ipynb` (Max) limpia los datos, arma 31 variables, separa por fecha
(60 % entrenamiento, 15 % validación y 25 % prueba), elige el umbral en validación y mide el costo en dinero: el
monto del fraude no detectado más USD 5 por alerta. Compara 7 modelos. La "Red 128-64" aparece dos veces en su
tabla, por eso parecían 8. La tabla ordena los modelos por el **costo en prueba**:

| Modelo (tabla de Max) | Costo en prueba (USD) |
|---|---|
| 7 · Árboles (HistGradientBoosting) | 392 |
| 4 · Red 64-32 sin peso de clase | 400 |
| 2 · Red 128-64 | 405 |
| 0 · Regresión logística | 415 |
| 3 · 200 épocas sin dropout | 415 |
| 5 · lr 0,01 | 455 |
| 1 · Red 64-32 | 478 |

## Por qué no se elige directo con esa tabla

1. **Prueba no sirve para elegir.** Se mira una sola vez, al final; si se usa para escoger, la nota queda inflada.
2. **La diferencia es ruido.** Hay 21 fraudes en validación y 46 en prueba. Con la misma red y el mismo reparto,
   pero otra semilla, el costo en prueba cambia mucho:

   | Red | Costo en prueba con 5 semillas (USD) |
   |---|---|
   | 1 · 64-32 | 405 – 572 |
   | 2 · 128-64 | 405 – 548 |
   | 3 · 200 épocas sin dropout | 435 – 529 |
   | 4 · sin peso de clase | 395 – 572 |
   | 5 · lr 0,01 | 390 – 504 |

   Los rangos se pisan entre sí: el orden de la tabla depende de la semilla. Incluso cambiar TensorFlow de
   2.22-rc0 a 2.21 reordena las redes.
3. **La validación del notebook se satura.** Todas las redes llegan a una PR-AUC cercana a 1,0 en validación, así
   que no sirve para distinguirlas.

## Cómo se eligió

- Se usaron solo las filas más antiguas (75 %). La prueba quedó intacta.
- **Validación cruzada por fecha:** se entrena con lo anterior y se valida con el bloque siguiente. Son 3 cortes,
  con 25, 10 y 21 fraudes en validación.
- **3 semillas** por red: 9 entrenamientos por modelo.
- **Métrica:** PR-AUC promedio, es decir, qué tan bien ordena por riesgo. El umbral no es parte del modelo: lo
  fija Riesgo en el dominio (RF3, umbrales 0,30 y 0,70) sin reconstruir la imagen.

### Resultado con las 31 variables del notebook

| Modelo | PR-AUC (media ± desv.) | ROC-AUC | Costo mínimo en validación (USD) |
|---|---|---|---|
| 7 · Árboles HGB | 0,994 ± 0,010 | 0,999 | 105 |
| 0 · Regresión logística | 0,964 ± 0,028 | 0,994 | 101 |
| 2 · Red 128-64 | 0,960 ± 0,036 | 0,984 | 111 |
| **4 · Red 64-32 sin peso de clase** | **0,958 ± 0,035** | 0,986 | 115 |
| 3 · 200 épocas sin dropout | 0,957 ± 0,040 | 0,982 | 111 |
| 1 · Red 64-32 | 0,951 ± 0,041 | 0,980 | 112 |
| Red 64-32 sin peso, con parada temprana | 0,950 ± 0,040 | 0,989 | 121 |
| 5 · lr 0,01 | 0,944 ± 0,045 | 0,972 | 158 |

## Decisión y motivos

- **Los árboles ordenan mejor (0,994)**, pero el curso exige PyTorch o TensorFlow. Quedan como **línea base**, junto
  con la regresión logística. La red no le gana a la logística: se usa por exigencia del curso, y porque se exporta a
  ONNX sin TensorFlow en la imagen.
- **Entre las redes, 128-64 y 64-32 sin peso empatan:** 0,960 y 0,958, con una diferencia mucho menor que la
  desviación (±0,035). Se elige la **64-32 sin peso de clase** por tres razones:
  1. tiene la mitad de los parámetros, así que la inferencia es más rápida y la imagen pesa menos;
  2. **sin peso de clase va con lo que pide Riesgo** (EP1, 1.3): "bajar los falsos positivos sin subir el fraude".
     Pesar más los fraudes empuja a la red a dar más alertas, que es lo contrario. Además, el umbral "óptimo" de las
     redes en el notebook varió entre 0,03 y 0,95 porque con 21 fraudes no es confiable. Por eso el umbral no se fija
     por modelo: lo fija Riesgo (0,30 revisar y 0,70 bloquear);
  3. en el pipeline da el mismo resultado con 3 semillas distintas (recall 0,933, falsos positivos 1,57 %).
- **Se descartan:**
  - lr 0,01: el peor costo y la menor PR-AUC;
  - 200 épocas sin dropout: tarda 3 veces más y no mejora;
  - la parada temprana: con tan pocos fraudes en validación fue inestable (con 7 variables: 0,841 ± 0,168).

## Lo que más mejora el modelo: las variables, no la red

PR-AUC con validación cruzada según qué variables recibe el modelo:

| Variables | Red 4 (elegida) | Red 128-64 | Logística | Árboles |
|---|---|---|---|---|
| 7 del sistema actual (monto, hora, intentos, dispositivo, país, canal) | 0,919 | 0,921 | 0,911 | 0,986 |
| 7 + dispositivo (emulador, SO inconsistente, antigüedad del dispositivo…) | 0,908 | 0,919 | 0,894 | 0,981 |
| **7 + usuario** (KYC, verificado, teléfono, edad de la cuenta, transacciones previas, correo desechable, país del usuario ≠ país de la transacción, sin registro, segmento) | **0,946** | 0,941 | 0,958 | 0,993 |
| 7 + usuario + dispositivo + mcc | 0,955 | 0,946 | 0,977 | 0,994 |
| Las 31 de Max | 0,958 | 0,960 | 0,964 | 0,994 |
| Las 31 sin banderas de calidad ni lista negra | 0,951 | 0,954 | 0,966 | 0,994 |

- Las **variables del usuario** suben la red de 0,919 a 0,946 y la hacen más estable. Ya están en la tabla
  `usuarios` de Postgres.
- Las de dispositivo no aportan.
- Las **banderas de calidad** (moneda inferida, monto negativo, coordenada o IP inválida) y las de **lista negra** no
  aportan nada medible, y en producción valen siempre 0: el dominio bloquea por lista negra antes de llamar al modelo
  y el Core no manda montos negativos. Conviene no usarlas.

**Siguiente mejora (fase 2):** agregar las variables del usuario. Hay que cambiar `datos/preparar.py`,
`servicio-ia/entrenar.py`, el contrato de `POST /predict` y `dominio/app/logica.py`, que buscaría al usuario en la
base.

## Para corregir en el notebook antes de subirlo

- Ordenar la tabla por la métrica de **validación**, no por el costo en prueba. Agregar la validación cruzada por
  fecha.
- Exportar el modelo **elegido**. Hoy exporta el 1 (64-32 con peso), que es el peor de la tabla.
- Descomentar las celdas de los experimentos, para que "ejecutar todo" reproduzca la tabla, y quitar la fila
  repetida.
- Usar los mismos costos que el pipeline y el informe:
  - **fraude no detectado = su monto**, como hace el notebook (es más preciso que el promedio de USD 57);
  - **falso positivo = USD 6,92**, que sale del caso (USD 1.162.600 al año / (460 bloqueos al día × 365)), en vez de
    los USD 5 supuestos.
- TensorFlow 2.21 estable y Python 3.12, como en el pipeline, en vez de 2.22.0-rc0 y Python 3.14.
