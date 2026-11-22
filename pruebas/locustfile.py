"""Prueba de carga de POST /api/predict contra el sistema desplegado en AWS (EP3, 4.3).

Nunca se prueba el LLM: con su limite por clave mediria el limite de NVIDIA y no el del sistema. Por eso las transacciones de esta
prueba son compras normales (se aprueban) y casi ninguna llega a la cola del LLM. El camino del LLM se mide aparte, con
scripts/probar-llm.py.

Como correrla (PowerShell, desde la raiz del repositorio):

    pip install locust==2.47.0
    New-Item -ItemType Directory -Force pruebas/resultados | Out-Null
    $env:API_KEY = "<la clave de la EC2>"
    locust -f pruebas/locustfile.py --host https://<ip-con-guiones>.sslip.io --headless -u 20 -r 5 -t 3m --csv pruebas/resultados/carga

Deja pruebas/resultados/carga_stats.csv con p50, p95, p99, solicitudes por segundo y errores, y carga_failures.csv con el detalle.

Antes de correrla sube LIMITE_POR_MINUTO en el .env de la EC2 (por ejemplo 100000) y haz `docker compose up -d gateway`; si no, el
rate limiting responde 429 y mides el limite del gateway, no el del sistema. Despues de la prueba vuelve a bajarlo. Los 429 que
aparezcan se informan aparte: son el limite funcionando.

Las transacciones salen de pruebas/pool.csv: 300 compras reales de la muestra (usuario, dispositivo y comercio que existen, ninguno en
la lista negra). A cada una se le pone una fecha distinta de los ultimos 90 dias: asi el contador de intentos de la ultima hora
(que usa el modelo) no sube mientras dura la prueba y no se disparan los bloqueos por repeticion.
"""
import csv
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

from locust import HttpUser, between, task

POOL = list(csv.DictReader(open(Path(__file__).with_name("pool.csv"), encoding="utf-8")))
CLAVE = os.getenv("API_KEY", "")


def fecha_distinta() -> str:
    """Un dia al azar de los ultimos 90, entre las 9 y las 20 h (hora de Chile, sin zona horaria)."""
    dia = datetime.now().replace(minute=random.randint(0, 59), second=random.randint(0, 59), microsecond=0) - timedelta(days=random.randint(1, 90))
    return dia.replace(hour=random.randint(9, 20)).isoformat()


class Cliente(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self):
        if not CLAVE:
            raise SystemExit("Falta la clave: en PowerShell, $env:API_KEY = '<la clave de la EC2>'")
        self.cabeceras = {"X-API-Key": CLAVE}

    @task
    def predecir(self):
        t = random.choice(POOL)
        cuerpo = {"usuario_id": t["usuario_id"], "dispositivo_id": t["dispositivo_id"], "comercio_id": t["comercio_id"],
                  "monto": float(t["monto_usd"]), "moneda": "USD", "canal": t["canal"], "pais_transaccion": "CL",
                  "fecha_hora": fecha_distinta()}
        with self.client.post("/api/predict", json=cuerpo, headers=self.cabeceras, name="POST /api/predict", catch_response=True) as r:
            if r.status_code == 200:
                if "decision" not in r.json():
                    r.failure("200 sin decision")
            elif r.status_code == 429:
                r.failure("429 rate limiting")          # el limite funcionando: se informa aparte
            else:
                r.failure(f"HTTP {r.status_code}")
