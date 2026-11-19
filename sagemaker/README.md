# sagemaker/ · la red de Randuin en un endpoint de SageMaker (EP3)

El modelo que se sirve es **la misma red 64-32 de EP2** (`docs/ELECCION-MODELO.md`), entrenada por un Training Job con
los datos tratados de `datos/preparar.py` y publicada en un endpoint serverless. El servicio de IA (`servicio-ia/`) deja de
cargar el modelo: lo invoca con `invoke_endpoint` usando el rol de la EC2, sin claves.

```
datos/preparar.py ──▶ 0_preparar.py ──▶ 1_subir.py ──▶ 2_entrenar.py ──▶ 3_endpoint.py ──▶ 4_probar.py
 (EP2, sin cambios)   train/validation   bucket S3      Training Job       serverless        cold start y
                      y prueba fija                      (entrenar_sm.py)   randuin-fraude    metricas
```

## Orden de trabajo

En tu computador (necesita solo Python): `python datos/preparar.py` y `python sagemaker/0_preparar.py`. En CloudShell
(`us-east-1`, rol `LabRole`), con esta carpeta subida como zip (*Actions → Upload file*):

```bash
python3 sagemaker/1_subir.py
python3 sagemaker/2_entrenar.py      # 2 a 5 min
python3 sagemaker/3_endpoint.py      # ~3 min en Creating
python3 sagemaker/4_probar.py
python3 sagemaker/5_borrar.py        # al terminar
```

Cada paso anota lo que hizo en `estado.json` (no se sube: trae el ID de la cuenta); si la sesión se corta, se vuelve a
correr el mismo paso.

## Decisiones que se defienden

| Decisión | Dónde | Por qué |
|---|---|---|
| El escalado va **dentro** de la red | `entrenar_sm.py` | Entrenamiento y servicio no pueden calcularlo distinto; el servicio de IA no necesita scikit-learn ni pandas |
| Misma separación por fecha que EP2 | `0_preparar.py` | La red se mide con las mismas 300 filas de prueba (45 fraudes) que en el informe |
| La prueba fija nunca se sube a S3 | `1_subir.py` | La red no puede verla |
| Concurrencia 10 y memoria 2048 MB | `3_endpoint.py` | 92 tx/s × 0,06 s ≈ 5,5 invocaciones a la vez; la memoria se mide (1024 vs 2048) |
| Compuerta de calidad de EP2 | `4_probar.py` | PR-AUC ≥ 0,80, recall ≥ 0,70 y no peor que el vigente |
| Plan B de tiempo real | `3_endpoint.py --tiempo-real` | Si el Learner Lab rechaza serverless (EP3, 2.2 regla 2) |

## Variables

`ENDPOINT` (por defecto `randuin-fraude`), `IMAGEN_ENTRENAMIENTO` e `IMAGEN_INFERENCIA` (las imágenes de TensorFlow de AWS
Deep Learning Containers; los tags están en `comun_sm.py`). No hay access keys en ninguna parte.
