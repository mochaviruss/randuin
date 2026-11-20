# 4. Integración y seguridad (IE7)

> Borrador para el informe de EP3. Los datos salen del código del repositorio (tag `ep3`).

## 4.1 Flujo principal

```mermaid
sequenceDiagram
    autonumber
    actor C as Cliente
    participant K as Caddy (HTTPS)
    participant G as Gateway
    participant D as Dominio
    participant I as Servicio IA
    participant S as SageMaker
    participant Q as RabbitMQ
    participant W as Worker
    participant N as LLM (NVIDIA)

    C->>K: POST /api/predict (X-API-Key)
    K->>G: reenvía
    G->>G: clave válida, límite por IP
    G->>D: POST /api/predict
    D->>D: lista negra y reglas duras
    D->>I: predecir con razones (timeout 100 ms)
    I->>S: invoke_endpoint (8 filas)
    S-->>I: probabilidades
    I-->>D: probabilidad + razones
    D->>D: umbrales → decisión, se guarda
    D-->>C: 200 decisión + razones + versión del modelo
    D-)Q: encola resumen (si es revisar o bloquear)
    Q->>W: mensaje
    W->>W: anonimiza
    W->>N: resumen en lenguaje común
    N-->>W: texto
    W->>D: PUT /interno/predicciones/{id}/llm
    Note over C,D: Camino de falla 1: SageMaker no responde a tiempo → política de reserva, origen "reserva"
    Note over W,N: Camino de falla 2: LLM con 429 o caído → reintentos, luego cola llm.fallidos y estado "degradado"; la decisión no cambia
```

El camino síncrono (pasos 1 a 12) no depende del LLM. El texto llega después, por la cola.

## 4.2 Tabla de rutas de la API

Todas pasan por el gateway. Están documentadas en `/docs` (OpenAPI).

| Ruta | Método | Clave | Qué hace | Respuestas |
|---|---|---|---|---|
| `/api/predict` | POST | Sí | Evalúa una transacción y devuelve la decisión | 200, 401, 422, 429 |
| `/api/predicciones/{id}` | GET | Sí | Decisión guardada y estado del resumen del LLM | 200, 401, 404 |
| `/api/revision` | GET | Sí | Cola de revisión priorizada | 200, 401 |
| `/api/metricas` | GET | Sí | Datos del dashboard | 200, 401 |
| `/api/health` | GET | No | Estado de base, SageMaker, cola y LLM | 200, 503 (solo si cae la base) |
| `/docs`, `/openapi.json` | GET | No | Documentación de la API | 200 |
| `/api/decision/*` | varias | Sí | Rutas de EP2 (umbrales, ejemplos, evaluación) | según ruta |
| `/api/ia/*` | varias | Sí, salvo `/api/ia/health` | Servicio de IA directo | según ruta |
| `/interno/*` | PUT/GET | — | Solo para el worker, **no se publica** | 404 desde afuera |

## 4.3 Autenticación y HTTPS

- **HTTPS.** Caddy es la única puerta a internet (puertos 80 y 443). Obtiene y renueva solo el certificado de Let's Encrypt para un nombre `sslip.io` basado en la IP elástica de la EC2. Ningún otro servicio publica puertos.
- **Clave de API.** Todas las rutas con datos exigen el header `X-API-Key`. La comparación se hace sobre el hash de ambas claves y en tiempo constante, para que no se pueda adivinar la clave midiendo tiempos. Si el servidor no tiene clave configurada, **nadie entra** (falla cerrada); no existe una clave por defecto.
- **Validación.** El cuerpo se valida con Pydantic y no admite campos extra. Un campo faltante o de tipo incorrecto da 422 con el detalle, **sin devolver el valor recibido** (así un número de tarjeta mal enviado no vuelve en la respuesta). El número de tarjeta no es un campo del sistema.

## 4.4 Rate limiting

El gateway limita las peticiones por IP de cliente con un balde de fichas: por defecto 120 por minuto en `/api/*`. Pasado el límite responde **429** con `Retry-After`. El límite se cambia en el `.env` (`LIMITE_POR_MINUTO`): se baja para demostrar el 429 y se sube para la prueba de carga, porque de lo contrario esa prueba mediría el límite del gateway y no el del sistema.

## 4.5 Manejo de secretos y anonimización

- **Secretos.** No hay secretos en el repositorio: `.env` está en `.gitignore` y el repositorio solo trae `.env.example` con los nombres de las variables. La clave de API, la contraseña de RabbitMQ y la clave de NVIDIA viven en el `.env` de la EC2.
- **Sin access keys en el servidor.** La EC2 llama a SageMaker con el rol de la instancia (`LabInstanceProfile`), no con claves de acceso. Las credenciales del Learner Lab no se guardan en ningún archivo.
- **Anonimización antes del LLM.** El dominio no incluye en el pedido el puntaje, el usuario, el dispositivo ni el comercio; solo el nivel de riesgo y las razones. Como última barrera, el worker pasa el texto por una función de anonimización que reemplaza RUT, correo, teléfono, número de tarjeta, IP y direcciones por marcadores antes de enviarlo a NVIDIA. Una prueba automatizada comprueba que ninguno de esos datos sobrevive (prueba 5 de 4.3).
- **Qué no se registra.** El número de tarjeta no entra al sistema ni a los registros, y la clave de NVIDIA no aparece en ningún mensaje de error (hay una prueba que lo verifica).

## 4.6 Pruebas automatizadas de esta sección

| # | Prueba (4.3 del enunciado) | Dónde está |
|---|---|---|
| 1 | Petición válida → 200 con resultado y versión del modelo | `dominio/tests/test_ep3.py` |
| 2 | Campo faltante o de tipo incorrecto → 422, sin devolver el valor | `dominio/tests/test_ep3.py` |
| 3 | Sin clave → 401 | `dominio/tests/test_ep3.py` y `gateway/prueba.js` |
| 4 | Ráfaga sobre el límite → 429 | `gateway/prueba.js` |
| 5 | Anonimización: RUT, correo, teléfono y tarjeta no sobreviven | `worker/tests/` |
| 6 | LLM con 429 o caído → la predicción responde igual y el estado queda degradado | `dominio/tests/test_ep3.py` y `worker/tests/` |

El pipeline ejecuta las pruebas del worker, del dominio, del gateway, del front y de las figuras en cada pull request.
