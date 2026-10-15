# Elección del modelo · Randuin (EP2)

**Decisión:** red neuronal **64-32 sin peso de clase** (modelo 4 del notebook `notebooks/randuin_modelo_tensorflow.ipynb`),
entrenada con TensorFlow y servida en ONNX. Es la configuración por defecto de `servicio-ia/entrenar.py`.

## Lo que comparó el notebook

El notebook sigue la guía del curso: separa por fecha (entrenamiento, validación y prueba), elige el umbral en
validación y mide el costo en dinero (fraude no detectado = su monto; cada alerta = USD 5). Probó 7 modelos:

| Modelo | Costo en prueba (USD) | Recall | PR-AUC |
|---|---|---|---|
| Árboles (HistGradientBoosting) | 392 | 0,957 | 0,975 |
| **Red 64-32 sin peso de clase** | **400** | 0,891 | 0,958 |
| Red 128-64 | 405 | 0,891 | 0,965 |
| Regresión logística | 415 | 0,891 | 0,965 |
| Red con 200 épocas y sin dropout | 415 | 0,913 | 0,955 |
| Red con tasa de aprendizaje 0,01 | 455 | 0,891 | 0,958 |
| Red 64-32 con peso de clase (la base) | 478 | 0,848 | 0,958 |

## Por qué la red 64-32 sin peso de clase

1. **El curso exige PyTorch o TensorFlow.** Los árboles salen primeros, pero no son una red: quedan como comparación
   en el informe, junto con la regresión logística.
2. **Entre las redes, las diferencias son muy chicas.** Hay solo 46 fraudes en prueba: un fraude más o menos cambia el
   costo en decenas de dólares. Al repetir el entrenamiento con otras semillas, el orden de la tabla cambia (la misma
   red da entre USD 395 y 572). No hay una red claramente mejor, así que se elige por otros criterios.
3. **Es la más simple**: la mitad de neuronas que la 128-64, así que predice más rápido y pesa menos.
4. **Va con lo que pide Riesgo** (EP1, 1.3): bajar los falsos positivos sin subir el fraude. Pesar más los fraudes
   (peso de clase) empuja a la red a dar más alertas, que es lo contrario. El umbral no se fija por modelo: lo fija
   Riesgo en la tabla `umbrales` (0,30 revisar, 0,70 bloquear).
5. **Da lo mismo cada vez que se entrena** (semilla fija): en el pipeline siempre sale recall 0,933 y 1,57 % de falsos
   positivos sobre la prueba fija.

En el pipeline (`entrenar.py`, con las 7 variables que recibe `/predict`) la red da sobre la prueba fija, con umbral
0,30: recall 0,933, precisión 0,913, falsos positivos 1,57 %, PR-AUC 0,955 y costo de errores USD 198,7. La
regresión logística, con el mismo preprocesador, da recall 0,867 y USD 397,4. La compuerta exige además no ser peor
que el modelo de la EP1 (XGBoost: recall 0,933, falsos positivos 1,96 %), y la red lo cumple.

## Lo que más mejoraría el modelo

No es cambiar la red: son las variables. El sistema usa 7; el notebook armó 31. Al probar la misma red con unas y
otras, las que más suben la PR-AUC son las del usuario (nivel KYC, cuenta verificada, antigüedad de la cuenta, correo
desechable), más que cualquier cambio de capas. Esos datos ya están en la tabla
`usuarios` de la base, así que es la mejora propuesta para EP3 (cambia `preparar.py`, `entrenar.py`, el contrato de
`/predict` y el dominio).

## Para corregir en el notebook antes de subirlo

- Ordenar la tabla por la métrica de **validación**, no por el costo en prueba: la prueba se mira una sola vez.
- Exportar el modelo **elegido** (hoy exporta la red base, la última de la tabla).
- Descomentar las celdas de los experimentos, para que "ejecutar todo" reproduzca la tabla.
- Usar TensorFlow 2.21 y Python 3.12, como el pipeline (el notebook usó 2.22.0-rc0 y Python 3.14).
