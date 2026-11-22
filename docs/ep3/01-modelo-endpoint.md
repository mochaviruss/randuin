# 1. Modelo y endpoint (IE1)

> Borrador para el informe de EP3. Los datos salen de `sagemaker/entrenar_sm.py`, `sagemaker/` y de las mediciones del 19 y 21 de noviembre de 2026. Capturas en `docs/ep3/capturas/`.

## 1.1 El modelo

| Aspecto | Detalle |
|---|---|
| Tipo | Red neuronal densa (MLP) de dos capas ocultas de 64 y 32 neuronas, activación ReLU, *dropout* de 0,2 y salida sigmoide: la probabilidad de fraude. |
| Framework | TensorFlow / Keras 2.21. |
| Por qué esta red | Es la elegida en EP2 entre 7 modelos comparados (docs/ELECCION-MODELO.md): el curso exige una red, es la más simple de las competitivas y su costo en dinero quedó entre los mejores. Se mantiene en EP3. |
| Cambio de EP3 | El logaritmo del monto y la estandarización de las variables son **capas dentro de la red**. Así el endpoint recibe las 10 columnas tal como salen del sistema y el servicio de IA ya no necesita scikit-learn, pandas ni onnxruntime (su imagen pasó de 759 MB a 256 MB). |
| Entradas | 7 variables, 10 columnas: monto en USD, hora local, intentos del usuario en la última hora, usuarios distintos en el dispositivo, sin dispositivo, país distinto de Chile y el canal (4 columnas 0/1). |
| Salida | Probabilidad de fraude entre 0 y 1. Los umbrales (0,30 para revisar y 0,70 para bloquear) viven en la base de datos, no en el modelo. |

## 1.2 Datos y separación de la prueba

El modelo se entrena con las transacciones etiquetadas del caso (usuarios, dispositivos y lista negra tratados por `datos/preparar.py`), **sin las variables que filtran la respuesta**: `revisado_por_analista` y `motivo_bloqueo` se llenan después de conocer el resultado y, si se usaran, el modelo aprendería la respuesta y no serviría en producción (riesgo R2 del caso). Tampoco se usan la IP ni el número de tarjeta enmascarado.

| Conjunto | Filas | Fraudes | Para qué |
|---|---|---|---|
| Entrenamiento | 720 | 69 | Ajustar los pesos. |
| Validación | 181 | 20 | Vigilar el entrenamiento (PR-AUC). |
| **Prueba fija** | 300 | 45 | Medir el modelo. Es el 25 % **más reciente** de lo etiquetado, así que la prueba simula el futuro. **Nunca se sube a S3**: la red no puede verla (`1_subir.py`). |

## 1.3 Hiperparámetros y entrenamiento

Lo corre un **Training Job de SageMaker** en una instancia `ml.m5.large` (se cobra solo lo que dura el job, de 2 a 5 minutos), con la imagen oficial de TensorFlow 2.21 de AWS y el script `sagemaker/entrenar_sm.py`.

| Hiperparámetro | Valor |
|---|---|
| Capas ocultas | 64 y 32 |
| *Dropout* | 0,2 |
| Optimizador | Adam, tasa de aprendizaje 0,001 |
| Épocas | 60 |
| Tamaño de lote | 64 |
| Peso de clase | Ninguno (Riesgo pide menos alertas, no más) |
| Semilla | 42, con operaciones deterministas: el mismo resultado en cada entrenamiento |

El modelo se guarda en el formato de TensorFlow Serving (`export/Servo/1`) y se sube a S3. Todo el proceso se repite con los scripts `sagemaker/0_preparar.py` a `3_endpoint.py`.

## 1.4 El endpoint

| Configuración | Valor |
|---|---|
| Nombre | `randuin-fraude` |
| Tipo | **Sin servidor** (serverless): no se paga cuando no hay tráfico |
| Memoria | **2 GB** |
| Concurrencia máxima | **10** invocaciones simultáneas |
| Versión | `randuin-fraude-20261119-172550` (el nombre de la configuración del endpoint) |
| Imagen de inferencia | TensorFlow Serving 2.20 de AWS |
| Acceso | Solo desde la EC2, con el rol `LabInstanceProfile` (sin claves de acceso) |
| Estado | **InService**, sin alarmas |

**Por qué estos valores.** Sin servidor porque el tráfico es muy desigual (picos de 92 por segundo y valles casi vacíos) y no se paga por una instancia ociosa. 2 GB porque es el tamaño que se probó primero; la medición muestra que el modelo usa solo ~6,5 % de esa memoria, así que **1 GB alcanzaría** y costaría la mitad (ver el apartado 5). Concurrencia 10 porque a 92 invocaciones por segundo y ~40 ms cada una se necesitan unas 4 al mismo tiempo; hay margen.

Capturas: `sagemaker-endpoint.png` (estado InService y configuración). La versión del modelo que decidió cada transacción viaja en la respuesta de `POST /api/predict` (campo `modelo_version`) y se guarda con la decisión.

## 1.5 Una invocación

El servicio de IA envía al endpoint **8 filas en una sola llamada**: la transacción original y 7 variantes en las que una variable cambia por su valor típico. Con las probabilidades de las 8 filas se calculan las razones de la decisión (apartado 2). Un ejemplo con una transacción sospechosa (monto de USD 52, 3 de la madrugada, 5 intentos en la última hora, 4 usuarios en el dispositivo, país extranjero) devolvió una probabilidad de **0,988** y la decisión `bloquear`.
