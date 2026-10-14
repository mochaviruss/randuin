-- Esquema de la base de Randuin (PostgreSQL 16). Lo aplica el servicio carga-datos al arrancar.
-- Todo usa "IF NOT EXISTS": si la carga corre dos veces, no rompe ni duplica nada.

-- ------------------------------------------------------------------ datos del caso (ya tratados)
CREATE TABLE IF NOT EXISTS usuarios (
    usuario_id            text PRIMARY KEY,
    fecha_registro        date,
    pais                  char(2) NOT NULL,
    segmento              text    NOT NULL,
    nivel_kyc             smallint,
    verificado            boolean,
    transacciones_previas integer,
    email_dominio         text,
    telefono_verificado   boolean,
    email_desechable      boolean NOT NULL
);

CREATE TABLE IF NOT EXISTS dispositivos (
    dispositivo_id     text PRIMARY KEY,
    tipo               text,
    sistema_operativo  text,
    primera_vez        timestamptz,
    usuarios_distintos integer NOT NULL,
    emulador           boolean,
    pais_declarado     char(2)
);

CREATE TABLE IF NOT EXISTS lista_negra (
    entrada_id   text PRIMARY KEY,
    tipo         text    NOT NULL,          -- dispositivo, ip, comercio o tarjeta
    valor        text    NOT NULL,
    fecha_alta   date,
    motivo       text,
    agregado_por text,
    vigente      boolean NOT NULL,
    UNIQUE (tipo, valor)
);

-- Historial de transacciones del caso. Sin ip ni tarjeta: no son variables del modelo y no se guardan (RNF5).
-- dispositivo_id no lleva FK porque hay transacciones con dispositivos que no estan en el catalogo.
CREATE TABLE IF NOT EXISTS transacciones (
    transaccion_id         text PRIMARY KEY,
    usuario_id             text NOT NULL REFERENCES usuarios (usuario_id),
    comercio_id            text,
    dispositivo_id         text,
    ts_utc                 timestamptz   NOT NULL,
    monto_usd              numeric(12,2) NOT NULL,
    moneda                 char(3)       NOT NULL,
    canal                  text          NOT NULL,
    mcc                    text,
    pais_transaccion       char(2),
    latitud                double precision,
    longitud               double precision,
    resultado_autorizacion text,
    intentos_previos_1h    integer,
    es_reintento           boolean NOT NULL,
    es_fraude              smallint        -- vacio = nunca la reviso un analista
);

-- ------------------------------------------------------------------ datos que produce el sistema
-- Umbrales de Riesgo (RF3). No se edita una fila: cada cambio es una fila nueva con quien, cuando y por que
-- (RNF10, RO5). La vigente es la de mayor version.
CREATE TABLE IF NOT EXISTS umbrales (
    version         serial PRIMARY KEY,
    umbral_revisar  numeric(3,2) NOT NULL,
    umbral_bloquear numeric(3,2) NOT NULL,
    autorizado_por  text         NOT NULL,   -- quien
    creado_en       timestamptz  NOT NULL DEFAULT now(),   -- cuando
    motivo          text         NOT NULL    -- por que
);

-- Cada prediccion con su entrada, su resultado, la version del modelo y la fecha (RF7, enunciado 2.1).
-- La escribe solo el microservicio de dominio.
CREATE TABLE IF NOT EXISTS predicciones (
    id                bigserial PRIMARY KEY,
    transaccion_id    text        NOT NULL UNIQUE,
    creado_en         timestamptz NOT NULL DEFAULT now(),
    fecha_transaccion timestamptz NOT NULL,
    usuario_id        text        NOT NULL REFERENCES usuarios (usuario_id),
    dispositivo_id    text,
    comercio_id       text,
    monto_usd         numeric(12,2) NOT NULL,
    canal             text        NOT NULL,
    entrada           jsonb       NOT NULL,   -- lo que llego a POST /evaluar
    variables         jsonb,                  -- lo que se envio al modelo (POST /predict)
    score             numeric(5,4),           -- vacio si no se consulto al modelo
    decision          text        NOT NULL,   -- aprobar, revisar o bloquear
    origen            text        NOT NULL,   -- modelo, lista_negra o reserva
    razones           jsonb       NOT NULL,
    modelo_version    text,
    umbral_version    integer REFERENCES umbrales (version),
    replica_ia        text,                   -- que replica de ia respondio
    latencia_ms       numeric(8,2)            -- tiempo total de la decision dentro del dominio
);
CREATE INDEX IF NOT EXISTS ix_predicciones_usuario ON predicciones (usuario_id, fecha_transaccion);

-- Umbrales iniciales, solo si la tabla esta vacia.
INSERT INTO umbrales (umbral_revisar, umbral_bloquear, autorizado_por, motivo)
SELECT 0.30, 0.70, 'Gerencia de Riesgo', 'umbrales iniciales de Riesgo'
WHERE NOT EXISTS (SELECT 1 FROM umbrales);
