# Randuin · detección de fraude para Kipu Pagos

ITY1102 Arquitectura de Sistemas IA · EP2 · Equipo 07 (Ian Villalobos, Maximiliano Rodríguez, George Castillo)

Randuin evalúa cada transacción antes de autorizarla y responde **aprobar**, **revisar** o **bloquear**, con las razones
que explican la decisión. Es la primera versión del diseño arc42 de la EP1 (caso 3, Kipu Pagos SpA), corriendo en
contenedores.

## Levantar el sistema (computador de la defensa)

```powershell
docker compose -p equipo07 pull
docker compose -p equipo07 up -d
```

Abrir <http://localhost:8080>. No hace falta crear `.env`: los valores por defecto funcionan (ver `.env.example`).
Para apagar: `docker compose -p equipo07 down -v`.

## Las piezas (igual que la figura 4 del informe)

```
navegador ──8080──▶ gateway (Node) ──/───────────────▶ front (página HTML)
                         │ ──/api/decision/──▶ dominio (FastAPI) ──▶ postgres (volumen datos-db)
                         │                        │
                         │ ◀──/api/ia/predict─────┘   el dominio llama al modelo A TRAVÉS del gateway
                         └──/api/ia/──────────▶ ia ×2 réplicas (FastAPI + onnxruntime)
carga-datos (una sola vez): crea las tablas, inserta los datos tratados y termina → recién ahí arranca el dominio
```

| Servicio | Imagen | Qué hace | Límite (7.2) |
|---|---|---|---|
| gateway | `ghcr.io/mochaviruss/gateway:ep2` | Única puerta (8080). Enruta y reparte entre las 2 réplicas de ia. Exige API key para el modelo. Es el gateway Node del L6. | 0,5 CPU · 128 MiB |
| front | `ghcr.io/mochaviruss/front:ep2` | Formulario para evaluar transacciones y ver las últimas decisiones. | 0,25 CPU · 64 MiB |
| dominio | `ghcr.io/mochaviruss/dominio:ep2` | Lista negra, variables, llamada al modelo, umbrales, política de reserva. Único que lee y escribe la base. | 0,5 CPU · 160 MiB |
| ia | `ghcr.io/mochaviruss/servicio-ia:ep2` | Red neuronal (TensorFlow → ONNX): probabilidad de fraude, 3 razones y versión del modelo. 2 réplicas. | 1 CPU · 320 MiB c/u |
| postgres | `postgres:16-alpine` | Datos del caso, predicciones y umbrales. Volumen `datos-db`. | 0,25 CPU · 192 MiB |
| carga-datos | `ghcr.io/mochaviruss/carga-datos:ep2` | Corre una vez (`restart: "no"`). No duplica filas (`ON CONFLICT DO NOTHING`). | 0,5 CPU · 96 MiB |

## Rutas y contratos

| Ruta pública (gateway) | Va a | Para qué |
|---|---|---|
| `/` | front | la página |
| `POST /api/decision/evaluar` | dominio `/evaluar` | decisión completa (flujo principal) |
| `GET /api/decision/predicciones`, `/predicciones/{id}` | dominio | decisiones guardadas |
| `GET`/`POST /api/decision/umbrales` | dominio | ver o cambiar umbrales (con `autorizado_por` y `motivo`) |
| `GET /api/ia/health` | ia `/health` | estado del modelo y qué réplica respondió |
| `POST /api/ia/predict` | ia `/predict` | **exige el header `X-API-Key`** (sin él: 401) |

`POST /api/ia/predict` (contrato; si algo no cumple, 422):
```json
{"monto_usd": 52, "hora_local": 3, "intentos_previos_1h": 5, "dispositivo_usuarios_distintos": 4,
 "sin_dispositivo": false, "pais_extranjero": true, "canal": "web"}
```
→ `score` (probabilidad 0 a 1), `prediccion` (`fraude`/`legitima`), `razones` (las 3 de más peso),
`modelo_version`, `replica`, `latencia_ms`.

`POST /api/decision/evaluar`:
```json
{"usuario_id": "KP303245", "dispositivo_id": "D-5d47ee1b2e46", "comercio_id": "CM40413",
 "monto": 12000, "moneda": "CLP", "canal": "app", "pais_transaccion": "CL"}
```
→ `decision`, `origen` (`modelo`, `lista_negra` o `reserva`), `score`, `razones`, `modelo_version`, `replica_ia`,
umbrales usados y `latencia_ms`.

## Cómo decide el dominio (en orden)

1. Valida la entrada (422 si no cumple) y que el usuario exista (404).
2. **Lista negra**: si el dispositivo o el comercio están en ella → `bloquear`, sin consultar al modelo.
3. **Variables**: intentos del usuario en la última hora, usuarios del dispositivo, hora en Chile, país, canal (las
   mismas reglas que `datos/preparar.py`).
4. **Modelo**, por el gateway, con un tope de **100 ms**. Si no responde: **política de reserva** (aprueba, salvo 4 o
   más intentos en la hora o un dispositivo con 5 o más usuarios → revisar).
5. **Umbrales** vigentes de Riesgo (0,30 revisar · 0,70 bloquear al inicio), que se cambian sin redesplegar.
6. Guarda la predicción completa en la tabla `predicciones`.

## Comandos para la defensa (PowerShell)

```powershell
docker compose -p equipo07 ps -a                       # todo healthy; carga-datos "Exited (0)"
.\scripts\carga.ps1                                    # bucle de peticiones 60 s (en otra terminal: docker stats)
docker stats --no-stream                               # consumo real: pico x 1,5 = mem_limit
docker inspect --format '{{.State.OOMKilled}}' equipo07-ia-1   # true = el kernel lo mato por memoria (codigo 137)
.\scripts\latencia.ps1                                 # p50 y p95 de una predicción por el gateway
docker stop equipo07-ia-1                              # detener UNA réplica: el front sigue respondiendo
docker start equipo07-ia-1
docker compose -p equipo07 stop ia                     # detener AMBAS: el gateway responde 502 y el dominio usa la reserva
docker compose -p equipo07 start ia
docker compose -p equipo07 logs gateway --tail 20      # qué réplica atendió cada petición y los 5xx
docker compose -p equipo07 exec postgres psql -U randuin -d randuin -c "SELECT transaccion_id, creado_en, decision, origen, score, modelo_version FROM predicciones ORDER BY id DESC LIMIT 5;"
docker image inspect ghcr.io/mochaviruss/servicio-ia:ep2 | Select-String revision   # commit del que salió la imagen
```

Llamar al modelo a mano (PowerShell):
```powershell
$t = '{"monto_usd":52,"hora_local":3,"intentos_previos_1h":5,"dispositivo_usuarios_distintos":4,"pais_extranjero":true,"canal":"web"}'
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/ia/predict -ContentType 'application/json' -Headers @{'X-API-Key'='randuin-local'} -Body $t
```
También está la colección de Postman `postman/randuin.postman_collection.json` (operación, decisión, predicción,
contrato y carga), con el mismo esquema que la del curso.

## Monitoreo con comandos de Docker (8.1 del informe)

Método RED (Rate, Errors, Duration) + saturación + modelo. Cada número avisa si se sostiene **5 minutos**.

```powershell
# RATE · ¿cuánto trabajo recibe?  (avisa sobre 40 tx/s, lo que sostuvo el compose en el 7.2)
$l = docker compose -p equipo07 logs gateway --since 1m
"{0:N1} decisiones por segundo" -f (($l | Select-String 'POST /api/decision/evaluar').Count / 60)

# ERRORS · ¿qué parte falla?  (avisa sobre 1 %; la línea dice qué servicio falló)
$l = docker compose -p equipo07 logs gateway --since 5m
$e = $l | Select-String ' 5\d\d '
"{0} de {1} peticiones con 5xx = {2:P1}" -f $e.Count, $l.Count, ($e.Count / [math]::Max(1, $l.Count))
$e | Select-Object -Last 5 | ForEach-Object Line

# DURATION · ¿cuánto tarda el usuario que peor lo pasa?  (avisa sobre 100 ms, RNF1)
# + % de decisiones por reserva (avisa sobre 1 %) y % de bloqueos (RF12, avisa sobre 10 %), últimos 5 minutos
docker compose -p equipo07 exec postgres psql -U randuin -d randuin -c "SELECT count(*) AS decisiones, round((percentile_cont(0.95) WITHIN GROUP (ORDER BY latencia_ms))::numeric, 1) AS p95_ms, round(100.0 * avg((origen = 'reserva')::int), 1) AS pct_reserva, round(100.0 * avg((decision = 'bloquear')::int), 1) AS pct_bloqueo FROM predicciones WHERE creado_en > now() - interval '5 minutes';"
.\scripts\latencia.ps1                 # p95 de /predict visto desde fuera, por el gateway (bucle de curl)

# SATURACIÓN · ¿le queda espacio?  (avisa sobre 80 % de memoria del mem_limit o CPU de ia sobre 90 %)
docker stats --no-stream
docker inspect --format '{{.State.OOMKilled}}' equipo07-ia-1   # true = lo mató el kernel por memoria (137)

# MODELO · ¿sigue acertando?  drift: media del día contra la del entrenamiento (29,3 USD, desviación 47,2;
# avisa si pasa de 29,3 + 2 × 47,2 = 123,6 USD). Las medias están en GET /api/ia/modelo.
docker compose -p equipo07 exec postgres psql -U randuin -d randuin -c "SELECT count(*) AS decisiones, round(avg((variables->>'monto_usd')::numeric), 1) AS monto_medio_usd FROM predicciones WHERE creado_en > now() - interval '1 day' AND variables IS NOT NULL;"
```

**p95**: se ordenan los tiempos de menor a mayor y se toma el que deja al 95 % por debajo (de 100, el 95; de 50, el
48). 1 de cada 20 usuarios espera más que eso. No se usa el promedio porque esconde a los lentos.

## Desarrollo local

```bash
pip install -r datos/requirements.txt -r servicio-ia/requirements-tensorflow.txt   # TensorFlow solo CPU
python datos/preparar.py                 # tratamiento: tablas para la carga + datos/salida/etiquetadas.csv
python servicio-ia/entrenar.py           # red TensorFlow -> compuerta -> ONNX en servicio-ia/model/
python -m pytest -q datos/tests docs/tests
(cd servicio-ia && python -m pytest -q tests)
node gateway/prueba.js && node front/prueba.js
# carga-datos y dominio necesitan una base de prueba en TEST_DB_URL (el pipeline la levanta sola)
docker compose -p equipo07 -f compose.yaml -f compose.build.yaml up -d --build      # construir en vez de descargar
```

## Pipeline (GitHub Actions, enunciado 2.5)

- `.github/workflows/ci.yml` (gateway, front, dominio, carga-datos): job `pruebas` (tratamiento de datos, tests con
  una base PostgreSQL real, tests del gateway, del front y de las figuras) → job `imagenes` (`needs: pruebas`), como en el L4.
- `.github/workflows/servicio-ia.yml` (como el de la guía del curso): tratamiento → `entrenar.py` con TensorFlow
  **solo CPU** y compuerta (PR-AUC ≥ 0,80, recall ≥ 0,70 y no peor que `servicio-ia/modelo_vigente.json`) → tests →
  build → prueba de humo (health, predict, imagen < 1 GB) → publicación.

| Evento | Qué hacen |
|---|---|
| PR hacia `dev` o `main` | tests + build, **sin publicar**. Si falla, el PR no se integra |
| push a `main` | lo mismo y publica en GHCR con `sha-xxxxxxx` y `latest` |
| tag `ep2` | lo mismo y publica con el tag `ep2` (el que usa el `compose.yaml`) |

Ramas: `feat/<descripción>` → PR a `dev` → PR de `dev` a `main` (protegida) → tag `ep2`.

## Carpetas

`gateway/` · `front/` · `dominio/` · `servicio-ia/` · `carga-datos/` · `datos/` (crudos, `preparar.py`, tests) ·
`docs/arquitectura/` (las figuras del informe en Mermaid) · `docs/ELECCION-MODELO.md` · `notebooks/` (experimentos) ·
`scripts/` (carga y latencia) · `postman/`. Cada servicio sin Python tiene su test en `prueba.js` (gateway y front).
