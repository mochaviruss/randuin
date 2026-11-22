# 5. Pruebas y calidad (IE10)

> Borrador para el informe de EP3. Mediciones del 21 de noviembre de 2026 sobre el sistema en AWS.

## 5.1 Casos de prueba automatizados

El pipeline (GitHub Actions, `ci` y `servicio-ia`) ejecuta todas las pruebas en cada pull request y **no publica las imágenes si alguna falla**. Hay **más de 200 casos**; el último pipeline sobre `main` terminó en verde.

| Conjunto | Casos | Qué cubre |
|---|---|---|
| Worker | 41 | Anonimización, reintentos ante 429 y 5xx, modo degradado, cola de fallidos |
| Dominio | 34 | Reglas, lista negra, reserva, validaciones, rutas de EP3, cola y LLM caído |
| Gateway | 57 | Rutas, clave de API, rate limiting (429), `/interno` cerrado |
| Front | 58 | Rutas, seguridad (sin `innerHTML`, CSP), celular, pantalla de entrada con clave |
| Servicio de IA | 11 | Contrato de `/predict`, razones, 503 si SageMaker no responde |
| Carga de datos | 3 | Cargar dos veces no duplica filas (con PostgreSQL real) |
| Figuras | 3 | La figura 4 tiene los mismos servicios que el `compose.yaml` |
| Tratamiento de datos | varios | Sin duplicados ni nulos en las columnas clave |

**Las seis pruebas obligatorias (4.3 del enunciado)**

| # | Prueba | Resultado esperado | Dónde |
|---|---|---|---|
| 1 | Petición válida a `POST /api/predict` | 200 con resultado y versión del modelo | `dominio/tests/test_ep3.py` |
| 2 | Campo faltante o de tipo incorrecto | 422 con el detalle, sin devolver el valor recibido | `dominio/tests/test_ep3.py` |
| 3 | Petición sin clave | 401 | `dominio/tests/test_ep3.py` y `gateway/prueba.js` |
| 4 | Ráfaga sobre el límite | 429 | `gateway/prueba.js`; y en la prueba de carga real (5.2) |
| 5 | Anonimización de RUT, correo, teléfono y tarjeta | Ninguno sobrevive | `worker/tests/test_anonimizar.py` |
| 6 | LLM con 429 o caído | La predicción responde igual y el estado queda degradado | `dominio/tests/test_ep3.py` y `worker/tests/test_worker.py` |

## 5.2 Rendimiento: prueba de carga con Locust

Contra `POST /api/predict` del sistema desplegado en AWS (EC2 `t3.medium`, endpoint de SageMaker sin servidor), desde un computador en Chile por HTTPS. Las transacciones son compras normales (para no medir el límite del LLM). Cada corrida dura 3 minutos. Los resultados (CSV de Locust) están en `pruebas/resultados/`; las cifras de abajo salen de esos archivos.

| Corrida | Usuarios | Peticiones | Req/s | Fallas | p50 | p95 | p99 | Máximo |
|---|---|---|---|---|---|---|---|---|
| Sostenida | 40 | 5.898 | 32,9 | 0 % | 180 ms | 260 ms | 420 ms | 850 ms |
| Pico | 110 | 14.007 | 78,2 | 0 % | 320 ms | 760 ms | 950 ms | 1.200 ms |
| Límite de peticiones (rate limiting activo, 120 por minuto) | 40 | 6.242 | 34,8 | 92,4 % con 429 (5.765) | 130 ms | 180 ms | 230 ms | 860 ms |

Las latencias incluyen el viaje de ida y vuelta entre Chile y Virginia (**~130 ms de base**). La tercera fila es la prueba de rate limiting funcionando bajo carga: pasaron solo 477 peticiones (unas 160 por minuto: el balde del gateway deja una ráfaga inicial y repone 2 por segundo) y el resto recibió 429 de inmediato.

**Lo que ve el servidor** (dashboard del sistema, última hora, que suma las tres corridas y las pruebas previas): 20.537 decisiones evaluadas, **13,7 % por la política de reserva**, latencia del dominio p50 91 ms, p95 390 ms y p99 519 ms.

**Cómo se lee.** El sistema no falló: ninguna petición dio error y SageMaker devolvió cero errores 4XX y 5XX. Pero a ~78 peticiones por segundo hay dos problemas frente al caso: (1) el p99 del dominio, 519 ms, supera los 100 ms que fija el caso para el percentil 99; (2) una de cada siete decisiones salió por la reserva en vez del modelo, porque la IA no respondió en 100 ms. Las causas probables, según CloudWatch: SageMaker levanta contenedores nuevos al subir la carga de golpe (`OverheadLatency` de 5 a 6 s al inicio de cada corrida) y la EC2 de 2 vCPU comparte máquina con la base de datos y los demás contenedores.

## 5.3 Métricas del modelo (prueba fija de 300 transacciones, 45 fraudes)

Con umbral de 0,30, medidas sobre el endpoint de SageMaker. La exactitud (*accuracy*) **no sirve para decidir**: "nunca es fraude" acierta 85 % en esta prueba y el 99,988 % en producción.

| Métrica | Valor |
|---|---|
| Matriz de confusión | 42 fraudes detectados · 3 no detectados · 6 falsos positivos · 249 aprobadas bien |
| Recall (fraude detectado) | **0,933** |
| Precision | **0,875** |
| F1 | 0,903 |
| PR-AUC | **0,953** |
| Tasa de falsos positivos | 2,35 % (6 de 255 compras legítimas) |
| Costo de los errores | USD 212,5 (el fraude no detectado cuesta su monto y cada alerta USD 5) |

Contra el modelo de EP2 (que en la misma prueba daba 4 falsos positivos y USD 198,7): el recall es igual y hay 2 falsos positivos más. La diferencia es chica con solo 45 fraudes en la prueba; un caso más o menos cambia el costo en decenas de dólares. Hay que leerlo con cautela: son datos de muestra y la prueba es pequeña.

## 5.4 Oportunidades de optimización

| # | Optimización | Qué resuelve | Costo | Riesgo |
|---|---|---|---|---|
| 1 | **Corte rápido (*circuit breaker*) en el dominio** | Cuando SageMaker está frío o caído, cada petición espera los 100 ms antes de la reserva (~104 ms). Un corte que deje de consultar al modelo durante unos segundos tras varias fallas baja esa espera a casi cero. | Sin costo de nube; ~1 día de desarrollo y pruebas | Poco: la política de reserva ya existe |
| 2 | **Concurrencia aprovisionada en SageMaker** (1 unidad) | Elimina el cold start y el que provoca la escalada: los contenedores quedan siempre calientes. | ~USD 26 al mes por 1 unidad de 2 GB, más la tarifa de inferencia (estimado con el precio publicado; confirmar en la calculadora de AWS) | Se paga aunque no haya tráfico; no sirve para la demostración del cold start |
| 3 | **EC2 más grande y separar la base de datos** (`t3.xlarge`, +USD 91 al mes por máquina; la base de datos a un servicio administrado) | Quita la competencia por CPU entre la IA, el dominio y PostgreSQL, que es la causa probable del p95 de 390 ms | +USD 91 al mes por cada `t3.xlarge`; el costo de la base administrada no se calculó | Hay que repetir la prueba de carga para confirmar el efecto |
| 4 | **Bajar el endpoint de 2 GB a 1 GB** | El modelo usa ~6,5 % de la memoria: se paga el doble sin necesidad | **Ahorra** ~USD 24 al mes al volumen de 1 millón diario | Hay que verificar que la latencia no sube |

El orden de prioridad sería 1 y 4 (gratis o con ahorro), luego 3 y, solo si el caso exige cumplir el p99 de 100 ms de punta a punta, 2.
