# 2. Consumo e interpretabilidad (IE2)

> Borrador para el informe de EP3. Los datos de esta sección salen del código del repositorio (tag `ep3`); si algo cambia en el sistema, se corrige aquí.

## 2.1 Cómo usa la aplicación el resultado del modelo

`POST /api/predict` recibe una transacción y devuelve una de tres decisiones: **aprobar**, **revisar** o **bloquear**. La decisión la toma el dominio, en este orden:

1. **Lista negra.** Si el dispositivo, el comercio o la IP están vigentes en la lista negra, se bloquea sin consultar al modelo (RF4, RF8). El origen de la decisión queda como `lista_negra`.
2. **Modelo.** Si no hay regla dura, el servicio de IA llama al endpoint de SageMaker y devuelve una probabilidad de fraude entre 0 y 1.
3. **Umbrales.** La probabilidad se compara con dos umbrales que se leen de la base de datos, no del código (RF3): desde **0,30** la transacción va a revisión y desde **0,70** se bloquea. Se pueden cambiar con `POST /umbrales`, que pide quién lo autoriza y por qué, sin volver a desplegar.
4. **Política de reserva.** Si el endpoint no responde en `IA_TIMEOUT_MS` (100 ms, para que el núcleo corte a los 150 ms que fija RNF3), el sistema aplica la política de reserva por reglas y marca la decisión con origen `reserva`. Así el cliente recibe respuesta aunque SageMaker esté frío o caído.

El LLM **no participa en la decisión**. Solo redacta un texto después de que la decisión ya está tomada (sección 2.3).

## 2.2 Qué ve el usuario y cómo entiende por qué se decidió

La respuesta trae la decisión, el origen (`modelo`, `lista_negra` o `reserva`), la probabilidad, los umbrales vigentes, la versión del modelo (el nombre de la configuración del endpoint) y las **razones** ordenadas por peso (RF2). El front las muestra con ícono y texto, no solo con color, de modo que se entienden también desde un celular o con daltonismo.

**Cómo se calculan las razones.** Las calcula el modelo, no el LLM. Por cada transacción, el servicio de IA arma 8 filas: la original y 7 variantes en las que una sola variable se reemplaza por su valor típico del entrenamiento. Las 8 filas van en **una única invocación** al endpoint. La diferencia entre la probabilidad original y la de cada variante es lo que esa variable aporta al riesgo. Las variables que casi no cambian la probabilidad (menos de 0,005) o que ya tienen el valor típico se omiten. El resto se ordena de mayor a menor peso.

| Variable | Cómo se muestra |
|---|---|
| `monto_usd` | monto (USD) |
| `hora_local` | hora local |
| `intentos_previos_1h` | intentos del usuario en la última hora |
| `dispositivo_usuarios_distintos` | usuarios distintos en el dispositivo |
| `sin_dispositivo` | sin dispositivo identificado |
| `pais_extranjero` | país distinto de Chile |
| `canal` | canal |

Ejemplo de razón: «usuarios distintos en el dispositivo = 40 (sube el riesgo)». Esto responde a la pregunta del analista («¿por qué bloqueó esto?») con una causa que puede comprobar en los datos.

**Límites de la explicación.** Es una explicación por oclusión de una variable a la vez: no captura interacciones entre variables, y la probabilidad sale de un conjunto de entrenamiento con mucho más fraude que el real, así que sirve para **ordenar casos**, no como la probabilidad de que una transacción sea fraude. El front lo dice junto al resultado.

Además del resultado de cada transacción, la aplicación ofrece:

- **Cola de revisión** (`GET /api/revision`): lo marcado como revisar o bloquear, por prioridad = probabilidad × monto en USD. Cuando no hay probabilidad se usa un valor fijo: 1,0 para la lista negra, 0,8 para un bloqueo por reserva y 0,5 para una revisión por reserva.
- **Dashboard** (`GET /api/metricas`): decisiones, quién decidió, tasa de bloqueo por día (RF12), tasa de reserva y latencia del dominio.
- **Estado de cada pieza** (`GET /api/health`): base de datos, SageMaker, cola y LLM, por separado.

## 2.3 Qué agrega el LLM y qué pasa cuando no está

Para las transacciones en «revisar» o «bloquear», el dominio deja un mensaje en la cola `llm` de RabbitMQ y responde de inmediato. Un worker lo toma, **anonimiza** el texto como última barrera (sección 4.5) y le pide a un modelo de lenguaje de NVIDIA un resumen en lenguaje común para el analista (RF5, RNF6). El resumen se guarda en la decisión y aparece en la cola de revisión.

El LLM recibe el **nivel de riesgo** y las razones ya calculadas, no la probabilidad ni datos identificadores. Por eso no puede cambiar ni contradecir la decisión: solo la explica.

La decisión tiene un estado de resumen:

| Estado | Significa |
|---|---|
| `no_aplica` | La transacción se aprobó: no lleva resumen. |
| `pendiente` | La decisión ya está tomada y el resumen se está redactando. |
| `listo` | El texto está disponible. |
| `degradado` | El LLM falló (límite de uso, caída, respuesta vacía o cola no disponible). Se guarda el motivo. **La decisión y las razones no cambian.** |

Cuando el LLM falla, el worker reintenta con espera creciente (errores 429 y 5xx); si no se recupera, el mensaje pasa a la cola de fallidos `llm.fallidos` y el estado queda `degradado`. El analista sigue viendo la decisión, la probabilidad y las razones del modelo; solo falta el texto. Esto se probó con una prueba automatizada que simula un 429 y una caída del LLM (prueba 6 de 4.3).

**Medición con el modelo provisorio.** Con 5 solicitudes, la latencia mediana del LLM fue de unos **1,25 s**, medida desde un computador personal. Con el ritmo mínimo que impone el worker entre llamadas (1,6 s), la capacidad es de unos 37 resúmenes por minuto. A la tasa de marcados que se usa para dimensionar (2,4 % de las transacciones), el pico sería de unos 132 por minuto, así que la cola absorbe el pico y el resumen puede llegar con retraso, sin afectar la decisión. Estos valores se actualizan cuando el docente publique el modelo oficial.
