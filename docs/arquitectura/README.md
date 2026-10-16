# Arquitectura de Randuin · figuras del arc42 (EP2)

Cada figura vive como código Mermaid (`.mmd`) y se versiona en el mismo pull request que el cambio que describe
(laboratorio L5). GitHub dibuja los bloques de abajo; las imágenes del informe están en `png/`.
La figura 4 tiene exactamente los mismos servicios que `compose.yaml` (nodos con la clase `cont`): lo comprueba
`docs/tests/test_figuras.py` en cada pull request.

Para regenerar una imagen después de editar su `.mmd` (misma letra y tamaño que las del informe):

```bash
npx -p @mermaid-js/mermaid-cli mmdc -i 04-contenedores.mmd -o png/04-contenedores.png -c mermaid-config.json -b white -s 2 -w 1500
```

| Figura | Capítulo | Archivo |
|---|---|---|
| Figura 1 · Contexto empresarial | 3.1 | [`01-contexto-empresarial.mmd`](01-contexto-empresarial.mmd) |
| Figura 2 · Contexto técnico: canales y protocolos | 3.2 | [`02-contexto-tecnico.mmd`](02-contexto-tecnico.mmd) |
| Figura 3 · Bloques de construcción, nivel 1 | 5.1 | [`03-bloques-nivel1.mmd`](03-bloques-nivel1.mmd) |
| Figura 4 · Contenedores (C4 nivel 2) = servicios del compose.yaml | 5.2 | [`04-contenedores.mmd`](04-contenedores.mmd) |
| Figura 5 · Componentes del microservicio ia (C4 nivel 3) | 5.3 | [`05-componentes-ia.mmd`](05-componentes-ia.mmd) |
| Figura 6 · Secuencia del flujo principal y camino de falla | 6 | [`06-secuencia.mmd`](06-secuencia.mmd) |
| Figura 7 · Despliegue actual (Docker Compose) | 7.1 | [`07-despliegue-actual.mmd`](07-despliegue-actual.mmd) |
| Figura 8 · Despliegue objetivo en AWS (2 zonas) | 7.1 | [`08-despliegue-objetivo.mmd`](08-despliegue-objetivo.mmd) |
| Figura 9 · Pipeline y gitflow | 7.4 | [`09-pipeline.mmd`](09-pipeline.mmd) |
| Figura 10 · Modelo de datos | 8.4 | [`10-modelo-datos.mmd`](10-modelo-datos.mmd) |

## Figura 1 · Contexto empresarial · cap. 3.1

```mermaid
flowchart LR
  classDef persona fill:#08427b,stroke:#052e56,color:#fff
  classDef sistema fill:#1168bd,stroke:#0b4884,color:#fff
  classDef externo fill:#999,stroke:#6b6b6b,color:#fff

  riesgo(["<b>Gerencia de Riesgo</b><br/>Ignacia Bravo · fija y<br/>autoriza los umbrales (RO5)"]):::persona
  analistas(["<b>Analistas de fraude</b><br/>6 analistas, 08:00 a 22:00"]):::persona
  auditor(["<b>Auditor externo / CMF</b><br/>reconstruye decisiones pasadas"]):::persona
  core["<b>Core de Pagos</b><br/>pide la decisión antes de autorizar"]:::externo

  R["<b>Randuin</b><br/>decide aprobar, revisar o bloquear<br/>cada transacción, con sus razones,<br/>y registra cada decisión"]:::sistema

  legacy["<b>Motor de Reglas Legacy (Java)</b><br/>decide mientras Randuin está en sombra (RO6)"]:::externo
  herr["<b>Herramienta de analistas</b><br/>cola de revisión existente (se integra, §11)"]:::externo
  dw["<b>Data Warehouse</b><br/>histórico de 3 años, carga nocturna"]:::externo
  bureau["<b>Bureau de Identidad</b><br/>900 ms, USD 0,08 por consulta"]:::externo
  sanc["<b>Proveedor de Listas de Sanciones</b><br/>archivo diario"]:::externo

  core -->|"transacción → decisión y razones"| R
  riesgo -->|"umbrales: quién, cuándo y por qué"| R
  auditor -->|"traza de una transacción"| R
  sanc -->|"lista de sanciones"| R
  R -->|"transacciones marcadas y razones"| herr
  analistas -->|"revisan y etiquetan"| herr
  R -->|"decisiones para reentrenar"| dw
  R -.->|"consulta puntual, fuera de los 100 ms"| bureau
  core -.->|"decisión vigente en modo sombra / reserva"| legacy
```

## Figura 2 · Contexto técnico: canales y protocolos · cap. 3.2

```mermaid
flowchart LR
  classDef persona fill:#08427b,stroke:#052e56,color:#fff
  classDef sistema fill:#1168bd,stroke:#0b4884,color:#fff
  classDef externo fill:#999,stroke:#6b6b6b,color:#fff

  riesgo(["<b>Gerencia de Riesgo</b>"]):::persona
  analistas(["<b>Analistas de fraude</b>"]):::persona
  auditor(["<b>Auditor externo / CMF</b>"]):::persona
  core["<b>Core de Pagos</b><br/>Kotlin, cuenta PCI"]:::externo
  R["<b>Randuin</b>"]:::sistema
  legacy["<b>Motor de Reglas Legacy</b><br/>Java"]:::externo
  herr["<b>Herramienta de analistas</b>"]:::externo
  dw[("<b>Data Warehouse</b><br/>Redshift")]:::externo
  bureau["<b>Bureau de Identidad</b>"]:::externo
  sanc["<b>Listas de Sanciones</b>"]:::externo

  core -->|"HTTPS/JSON síncrono · p99 < 100 ms, tope 150 ms (RT3) · sin PAN (RT4)"| R
  riesgo -->|"HTTPS/JSON · umbrales con quién y motivo (RO5)"| R
  auditor -->|"HTTPS/JSON · por transaccion_id, < 1 h"| R
  sanc -->|"CSV diario (RT6)"| R
  R -->|"API REST/JSON · razones < 5 s (RNF6)"| herr
  analistas -->|"web interna"| herr
  R -->|"COPY SQL nocturno (RT7)"| dw
  R -.->|"HTTPS/REST · 900 ms · solo casos puntuales (RT5)"| bureau
  core -.->|"HTTP/JSON interno · sombra y reserva (RNF3, RO6)"| legacy
```

## Figura 3 · Bloques de construcción, nivel 1 · cap. 5.1

```mermaid
flowchart LR
  classDef bloque fill:#1168bd,stroke:#0b4884,color:#fff
  classDef externo fill:#999,stroke:#6b6b6b,color:#fff
  core["Core de Pagos"]:::externo -->|transacción| api
  riesgo(["Gerencia de Riesgo"]):::externo -->|umbrales| conf
  subgraph R["Randuin"]
    api["<b>API de Decisión</b><br/>reglas duras RF4/RF8, umbrales RF3,<br/>política de reserva RNF3<br/><i>EP2: contenedor dominio</i>"]:::bloque
    conf["<b>Servicio de Configuración<br/>de Umbrales</b><br/>bitácora: quién, cuándo, por qué (RNF10)<br/><i>EP2: dominio /umbrales,<br/>tabla umbrales</i>"]:::bloque
    motor["<b>Motor de Inferencia</b><br/>red MLP (TensorFlow → ONNX):<br/>score y razones (RF2)<br/><i>EP2: contenedor ia, 2 réplicas</i>"]:::bloque
    vel["<b>Contador de Velocidad</b><br/>intentos en la última hora (RF10)<br/><i>EP2: consulta en postgres</i>"]:::bloque
    aud[("<b>Repositorio de Auditoría</b><br/>cada decisión con entrada, modelo,<br/>umbral y fecha (RF7, RNF7)<br/><i>EP2: postgres, tabla predicciones</i>")]:::bloque
    api -->|"variables, timeout 100 ms"| motor
    api -->|"usuario, dispositivo"| vel
    api -->|registra| aud
    conf -->|umbrales vigentes| api
  end
```

## Figura 4 · Contenedores (C4 nivel 2) = servicios del compose.yaml · cap. 5.2

```mermaid
flowchart LR
  classDef persona fill:#08427b,stroke:#052e56,color:#fff
  classDef cont fill:#1168bd,stroke:#0b4884,color:#fff
  classDef ext fill:#999,stroke:#6b6b6b,color:#fff
  usuario(["<b>Analista / Gerencia de Riesgo</b><br/>[Persona] usa el front<br/>desde el navegador"]):::persona
  core["<b>Core de Pagos</b><br/>[Sistema externo]<br/>en producción"]:::ext
  subgraph randuin["Randuin · docker compose -p equipo07 · red interna equipo07_default"]
    gateway["<b>gateway</b><br/>[Contenedor: Node 22-alpine]<br/>única puerta y único puerto<br/>publicado (8080); API key en /api/ia"]:::cont
    front["<b>front</b><br/>[Contenedor: nginx 1.28-alpine]<br/>página HTML: formulario y<br/>últimas decisiones"]:::cont
    dominio["<b>dominio</b><br/>[Contenedor: Python 3.12 + FastAPI]<br/>API de Decisión: lista negra, umbrales,<br/>reserva; único que usa la base"]:::cont
    ia["<b>ia</b> · 2 réplicas<br/>[Contenedor: FastAPI + onnxruntime]<br/>red TensorFlow exportada a ONNX;<br/>score, 3 razones y versión; sin estado"]:::cont
    postgres[("<b>postgres</b><br/>[Contenedor: PostgreSQL 16-alpine]<br/>6 tablas en el volumen datos-db")]:::cont
    carga_datos["<b>carga-datos</b><br/>[Contenedor: Python 3.12 + psycopg]<br/>restart no: crea tablas y carga<br/>los CSV tratados una vez"]:::cont
  end
  usuario -->|"HTTP :8080<br/>GET / y /api/decision/..."| gateway
  core -->|"JSON/HTTP :8080<br/>POST /api/decision/evaluar"| gateway
  gateway -->|"HTTP :80 · GET /"| front
  gateway -->|"JSON/HTTP :8000<br/>/api/decision/*"| dominio
  dominio -->|"JSON/HTTP :8080<br/>POST /api/ia/predict<br/>X-API-Key · tope 100 ms"| gateway
  gateway -->|"JSON/HTTP :8000 · /api/ia/*<br/>round robin entre réplicas"| ia
  dominio -->|"SQL :5432<br/>consultas + INSERT"| postgres
  carga_datos -->|"SQL :5432<br/>ON CONFLICT DO NOTHING"| postgres
  style randuin fill:#ffffff,stroke:#8A8A8A,stroke-dasharray:5 5
```

## Figura 5 · Componentes del microservicio ia (C4 nivel 3) · cap. 5.3

```mermaid
flowchart LR
  classDef comp fill:#85bbf0,stroke:#5d82a8,color:#000
  classDef ext fill:#999,stroke:#6b6b6b,color:#fff
  dominio["<b>dominio</b><br/>[httpx] timeout 100 ms;<br/>si vence, política de reserva"]:::ext
  gateway["<b>gateway</b><br/>[Node] exige X-API-Key y reparte<br/>/api/ia/* entre las 2 réplicas"]:::ext
  subgraph ia["ia (servicio-ia) · cada réplica es idéntica y sin estado · Python 3.12 + FastAPI"]
    ruta["<b>Ruta POST /predict</b><br/>[FastAPI]<br/>orquesta y mide latencia_ms"]:::comp
    validador["<b>Validación de entrada</b><br/>[Pydantic: Transaccion]<br/>monto mayor que 0, hora 0-23,<br/>canal válido, sin campos extra;<br/>si no: 422"]:::comp
    vector["<b>Preprocesamiento</b><br/>[preprocesador.joblib]<br/>log del monto, estandarizar,<br/>one-hot del canal"]:::comp
    inferencia["<b>Inferencia</b><br/>[onnxruntime · modelo.onnx]<br/>red MLP 64-32 entrenada<br/>con TensorFlow → probabilidad"]:::comp
    explicacion["<b>Razones y respuesta</b><br/>[oclusión]<br/>cada variable a su valor típico:<br/>cuánto cambia el riesgo;<br/>3 razones (RF2) y versión"]:::comp
    modelo[("<b>Carga del modelo</b><br/>[modelo.onnx + preprocesador.joblib<br/>+ metadata.json] una vez al<br/>arrancar, dentro de la imagen")]:::comp
    salud["<b>GET /health · /modelo</b><br/>[FastAPI]<br/>503 sin modelo; versión,<br/>réplica y commit"]:::comp
  end
  dominio -->|"POST /api/ia/predict"| gateway
  gateway -->|"POST /predict :8000"| ruta
  gateway -->|"GET /health"| salud
  ruta -->|"1. valida el JSON"| validador
  validador -->|"2. si es válido"| vector
  vector -->|"3. matriz 8×10<br/>(original + 7 variantes)"| inferencia
  inferencia -->|"4. 8 probabilidades"| explicacion
  modelo -.->|"sesión ONNX ya cargada"| inferencia
  explicacion -->|"5. JSON de respuesta"| ruta
  style ia fill:#ffffff,stroke:#8A8A8A,stroke-dasharray:5 5
```

## Figura 6 · Secuencia del flujo principal y camino de falla · cap. 6

```mermaid
sequenceDiagram
  actor U as Analista (front)
  participant G as gateway
  participant D as dominio
  participant P as postgres
  participant I as ia (2 réplicas)

  U->>G: POST /api/decision/evaluar
  G->>D: POST /evaluar
  D->>P: usuario, umbrales, lista negra, intentos 1 h
  P-->>D: datos de contexto
  alt en lista negra (RF4, RF8)
    D->>D: bloquear sin llamar al modelo
  else sin coincidencia
    D->>G: POST /api/ia/predict + X-API-Key (tope 100 ms)
    alt alguna réplica responde a tiempo
      G->>I: POST /predict (round robin: 1, 2, 1, 2...)
      I-->>G: score, 3 razones, versión
      G-->>D: 200 + predicción
      D->>D: umbrales 0,30 / 0,70 → decisión
    else ninguna réplica responde o pasan 100 ms (RNF3)
      G-->>D: 502 "ia no responde" (o se corta a los 100 ms)
      D->>D: política de reserva
    end
  end
  D->>P: INSERT predicciones (entrada, resultado, versión, fecha)
  D-->>G: decisión, score, razones, versión
  G-->>U: 200 JSON
```

## Figura 7 · Despliegue actual (Docker Compose) · cap. 7.1

```mermaid
flowchart LR
  classDef cont fill:#1168bd,stroke:#0b4884,color:#fff
  classDef db fill:#1168bd,stroke:#0b4884,color:#fff
  classDef ext fill:#999,stroke:#6b6b6b,color:#fff
  ghcr[("<b>GHCR</b><br/>imágenes públicas :ep2<br/>del pipeline")]:::ext
  subgraph PC["Computador de la sala · Windows + Docker Desktop (linux/amd64)"]
    nav["Navegador<br/>http://localhost:8080"]
    cli["PowerShell<br/>docker compose -p equipo07 pull / up -d"]
    subgraph RED["Docker Engine · red interna equipo07_default"]
      gw["<b>gateway</b><br/>gateway:ep2 · 0,5 CPU · 128 MiB<br/><b>único puerto: 0.0.0.0:8080</b>"]:::cont
      fr["<b>front</b><br/>front:ep2 · 0,25 CPU · 64 MiB"]:::cont
      dom["<b>dominio</b><br/>dominio:ep2 · 0,5 CPU · 160 MiB"]:::cont
      ia1["<b>ia réplica 1</b> (equipo07-ia-1)<br/>servicio-ia:ep2 · 1 CPU · 320 MiB"]:::cont
      ia2["<b>ia réplica 2</b> (equipo07-ia-2)<br/>servicio-ia:ep2 · 1 CPU · 320 MiB"]:::cont
      pg[("<b>postgres</b><br/>postgres:16-alpine · 0,25 CPU · 192 MiB<br/>volumen con nombre datos-db")]:::db
      cg["<b>carga-datos</b><br/>carga-datos:ep2 · 0,5 CPU · 96 MiB<br/>restart no: corre una vez (Exited 0)"]:::cont
    end
  end
  cli -->|pull| ghcr
  nav -->|":8080"| gw
  gw -->|"/"| fr
  gw -->|"/api/decision/*"| dom
  gw -->|"/api/ia/* round robin"| ia1 & ia2
  dom -->|"POST /api/ia/predict + API key"| gw
  dom -->|"SQL :5432"| pg
  cg -->|"SQL :5432, una vez"| pg
```

## Figura 8 · Despliegue objetivo en AWS (2 zonas) · cap. 7.1

```mermaid
flowchart TB
  classDef svc fill:#1168bd,stroke:#0b4884,color:#fff
  classDef dat fill:#1168bd,stroke:#0b4884,color:#fff
  classDef ext fill:#999,stroke:#6b6b6b,color:#fff
  classDef lb fill:#08427b,stroke:#052e56,color:#fff
  core["<b>Core de Pagos</b><br/>cuenta PCI (RT1)"]:::ext
  legacy["<b>Motor de Reglas Legacy</b><br/>respaldo del core (RNF3, RO6)"]:::ext
  subgraph AWS["AWS us-east-1 · cuenta no PCI · VPC privada · orquestador ECS Fargate (ADR-7)"]
    alb{{"<b>Application Load Balancer interno</b> (2 zonas) · hace lo del gateway<br/>HTTPS · reglas: /api/decision/* · /api/ia/* · /<br/>health check GET /health cada 10 s, 2 fallas = fuera"}}:::lb
    subgraph AZa["Zona de disponibilidad us-east-1a"]
      d1["<b>dominio</b> tarea 1<br/>1 vCPU / 2 GB"]:::svc
      i1["<b>ia</b> tarea 1<br/>2 vCPU / 4 GB"]:::svc
      f1["<b>front</b> 1 tarea<br/>0,25 vCPU / 0,5 GB"]:::svc
      rds1[("<b>RDS PostgreSQL</b><br/>primaria, cifrada")]:::dat
    end
    subgraph AZb["Zona de disponibilidad us-east-1b"]
      d2["<b>dominio</b> tarea 2<br/>1 vCPU / 2 GB"]:::svc
      i2["<b>ia</b> tarea 2<br/>2 vCPU / 4 GB"]:::svc
      rds2[("<b>RDS PostgreSQL</b><br/>standby síncrona")]:::dat
    end
    s3[("<b>S3 Glacier Flexible Retrieval</b><br/>decisiones de más de 90 días,<br/>7 años (RN7)")]:::dat
    ecr[("<b>ECR</b> imágenes del pipeline<br/><b>Secrets Manager</b> clave de la base y API key<br/><b>CloudWatch</b> logs y alarmas (8.1)")]:::ext
  end
  core -->|"HTTPS privado, tope 150 ms (RT3)"| alb
  core -. "si Randuin no responde en 100 ms" .-> legacy
  alb -->|"/api/decision/*"| d1
  alb --> d2
  d1 -->|"POST /api/ia/predict, 100 ms"| alb
  d2 --> alb
  alb -->|"/api/ia/*"| i1
  alb --> i2
  alb -->|"/"| f1
  d1 -->|"SQL"| rds1
  d2 --> rds1
  rds1 -. "replicación síncrona, failover automático" .-> rds2
  rds1 -->|"export mensual"| s3
  style AZa fill:#eef4fc,stroke:#307FE2
  style AZb fill:#eef4fc,stroke:#307FE2
  style AWS fill:#ffffff,stroke:#8A8A8A
```

## Figura 9 · Pipeline y gitflow · cap. 7.4

```mermaid
flowchart LR
  A["Rama feat/<br/>(usuario de<br/>cada integrante)"] -->|PR| B{{"dev"}}
  subgraph CI["GitHub Actions · en cada PR a dev o main, push a main y tag ep2"]
    direction TB
    subgraph W2["ci.yml"]
      direction TB
      C1["<b>pruebas</b><br/>preparar.py · tests con Postgres real<br/>(carga sin duplicar, dominio)<br/>· test del gateway · figura 4 = compose"]
      C2["<b>imagenes</b> (needs: pruebas)<br/>gateway, front, dominio, carga-datos<br/>build amd64 + label revision"]
      C1 --> C2
    end
    subgraph W1["servicio-ia.yml"]
      direction TB
      S1["<b>datos</b> preparar.py"]
      S2["<b>entrenar.py</b> TensorFlow CPU → ONNX<br/>compuerta: PR-AUC ≥ 0,80, recall ≥ 0,70,<br/>no peor que el modelo vigente"]
      S3["<b>tests</b> · build amd64 + label revision<br/>· prueba de humo"]
      S1 --> S2 --> S3
    end
  end
  B -.-> C1 & S1
  S2 -. "no pasa la compuerta" .-> X["ALTO<br/>PR en rojo:<br/>no se integra"]
  C1 -. "un test falla" .-> X
  B -->|"PR dev → main"| M{{"main protegida"}}
  M -->|"push: publica"| G[("GHCR<br/>servicio:sha-xxxxxxx<br/>servicio:latest")]
  M -->|"tag ep2: publica"| I[("GHCR<br/>servicio:ep2")]
  I -->|"compose pull"| J["Computador<br/>de la defensa"]
```

## Figura 10 · Modelo de datos · cap. 8.4

```mermaid
erDiagram
  usuarios {
    text usuario_id PK
    date fecha_registro
    char pais
    text segmento
    smallint nivel_kyc
    boolean verificado
    boolean email_desechable
  }
  dispositivos {
    text dispositivo_id PK
    text tipo
    integer usuarios_distintos
    boolean emulador
  }
  transacciones {
    text transaccion_id PK
    text usuario_id FK
    text dispositivo_id "sin FK"
    timestamptz ts_utc
    numeric monto_usd
    text canal
    integer intentos_previos_1h
    smallint es_fraude "NULL = no revisada"
  }
  umbrales {
    serial version PK
    numeric umbral_revisar
    numeric umbral_bloquear
    text autorizado_por "quién"
    timestamptz creado_en "cuándo"
    text motivo "por qué"
  }
  predicciones {
    bigserial id PK
    text transaccion_id UK
    timestamptz creado_en "fecha"
    text usuario_id FK
    numeric monto_usd
    jsonb entrada "lo que llegó"
    jsonb variables "lo que vio el modelo"
    numeric score
    text decision "aprobar, revisar, bloquear"
    text origen "modelo, lista_negra, reserva"
    jsonb razones
    text modelo_version
    integer umbral_version FK
    text replica_ia
    numeric latencia_ms
  }
  lista_negra {
    text entrada_id PK
    text tipo "dispositivo, ip, comercio, tarjeta"
    text valor "UNIQUE con tipo"
    text motivo
    boolean vigente
  }
  umbrales ||--o{ predicciones : "versión aplicada"
  usuarios ||--o{ predicciones : "se evalúa"
  usuarios ||--o{ transacciones : "hace"
  dispositivos |o..o{ transacciones : "se usa en"
```
