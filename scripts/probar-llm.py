"""Prueba la API REAL de NVIDIA con TU clave, usando exactamente el mismo codigo que el worker (worker/app/llm.py).

Sirve para tres cosas: comprobar que la clave y el modelo funcionan, medir la latencia del LLM (va al informe, apartado 5) y
leer los textos que redacta, para juzgar si son utiles para un analista.

    pip install httpx                                 (una vez, si tu Python no lo tiene)
    python scripts/probar-llm.py                      # 5 llamadas
    python scripts/probar-llm.py 10                   # otra cantidad

La clave se pide por teclado y NO se muestra ni se guarda (tambien puede venir de la variable de entorno NVIDIA_API_KEY).
El modelo sale de NVIDIA_MODEL; si no esta, se usa el provisorio de .env.example. Cuando el docente publique el suyo, corre esto
de nuevo con NVIDIA_MODEL=<el del foro> y usa ESOS numeros en el informe.

Para no pasarse del limite de la capa gratuita (~40 por minuto) deja entre llamadas el mismo ritmo que el worker.
"""
from __future__ import annotations

import getpass
import importlib.util
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
from app import llm                              # noqa: E402  (despues de ajustar sys.path)
from app.anonimizar import anonimizar            # noqa: E402
from app.worker import Ritmo                     # noqa: E402

MODELO_PROVISORIO = "nvidia/nemotron-3.5-bolt-e4b"

# El pedido se arma con EL MISMO codigo que usa el dominio (dominio/app/resumen.py): asi la latencia y los textos que se miden son los del
# sistema real (nivel de riesgo, sin la probabilidad cruda). Se carga por ruta porque dominio y worker tienen ambos un paquete "app".
_spec = importlib.util.spec_from_file_location("resumen_dominio", Path(__file__).resolve().parents[1] / "dominio" / "app" / "resumen.py")
resumen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(resumen)

PEDIDO = resumen.armar_pedido(
    transaccion_id="TX-PRUEBA", decision="bloquear", origen="modelo", monto_usd=52, canal="web", hora_local=3,
    razones=[{"condicion": "intentos del usuario en la ultima hora = 5 (sube el riesgo)"},
             {"condicion": "usuarios distintos en el dispositivo = 4 (sube el riesgo)"},
             {"condicion": "monto (USD) = 52 (sube el riesgo)"}])
INSTRUCCION = PEDIDO["instruccion"]
# Datos personales de PRUEBA, agregados al final para ver que se enmascaran antes de salir (en el sistema real nunca llegan aqui).
CONTENIDO = (PEDIDO["contenido"] + "\n[Datos de prueba que NO deben salir: RUT 12.345.678-9, correo ana@correo.cl, "
             "tarjeta 4111 1111 1111 1111]")

AYUDA = {
    "HTTP 401": "la clave esta mal copiada, vencio o no corresponde a build.nvidia.com",
    "HTTP 404": "el nombre del modelo esta mal escrito",
    "HTTP 410": "NVIDIA retiro ese modelo del catalogo gratuito: pide otro o espera el del docente",
    "HTTP 400": "NVIDIA rechazo la peticion: revisa el nombre del modelo",
    "vacia": "el modelo respondio 200 sin texto: suele ser un modelo de razonamiento (content = null)",
    "Timeout": "el modelo tardo mas de NVIDIA_TIMEOUT (20 s por defecto): es lento para este uso",
    "429": "llegaste al limite de solicitudes por minuto de la capa gratuita",
}


def pista(motivo: str) -> str:
    return next((v for k, v in AYUDA.items() if k in motivo), "")


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    os.environ.setdefault("NVIDIA_MODEL", MODELO_PROVISORIO)
    if not os.getenv("NVIDIA_API_KEY"):
        os.environ["NVIDIA_API_KEY"] = getpass.getpass("Clave de NVIDIA (no se muestra): ").strip()
    print(f"Modelo: {llm.modelo_configurado()}  |  API: {os.getenv('NVIDIA_BASE_URL', 'https://integrate.api.nvidia.com/v1')}")
    print("\nLo que SALE hacia NVIDIA (ya anonimizado):")
    print("  " + anonimizar(CONTENIDO))
    print()

    ritmo, tiempos, fallas = Ritmo(), [], []
    for i in range(1, n + 1):
        ritmo.esperar()
        t0 = time.perf_counter()
        try:
            texto = llm.redactar(INSTRUCCION, CONTENIDO)
            ms = (time.perf_counter() - t0) * 1000
            tiempos.append(ms)
            print(f"[{i}/{n}] {ms:6.0f} ms  {texto[:260]!r}")
        except llm.LLMNoDisponible as e:
            fallas.append(str(e))
            print(f"[{i}/{n}] FALLA: {e}  ->  {pista(str(e))}")
            if any(c in str(e) for c in ("401", "404", "410", "400")):
                break                                   # no se arregla repitiendo

    print("\n=== Resumen ===")
    print(f"Respondieron bien: {len(tiempos)} de {n}")
    if tiempos:
        orden = sorted(tiempos)
        p95 = orden[min(len(orden) - 1, int(round(0.95 * (len(orden) - 1))))]
        print(f"Latencia: p50 {statistics.median(tiempos):.0f} ms | p95 {p95:.0f} ms | maxima {max(tiempos):.0f} ms "
              f"(incluye los reintentos ante 429, si los hubo)")
        print("Anota estos numeros: son la medicion de la latencia del LLM para el informe.")
    if fallas:
        print("Fallas:", "; ".join(sorted(set(fallas))))
    return 0 if tiempos and not fallas else 1


if __name__ == "__main__":
    sys.exit(main())
