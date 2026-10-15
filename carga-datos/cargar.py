"""Servicio carga-datos de Randuin: corre UNA vez y termina (restart: "no" en el compose).

1. Crea las tablas (schema.sql, todo con IF NOT EXISTS).
2. Inserta los CSV ya tratados por datos/preparar.py (vienen dentro de la imagen, en tratados/).
3. Termina con codigo 0: recien ahi el compose arranca el dominio
   (depends_on: condition: service_completed_successfully).

No duplica filas: cada INSERT lleva ON CONFLICT DO NOTHING sobre la clave primaria. Si el sistema se
levanta dos veces, la segunda carga inserta 0 filas nuevas.
"""
import csv
import os
import sys
import time
from pathlib import Path

import psycopg

AQUI = Path(__file__).resolve().parent
TRATADOS = Path(os.getenv("CARPETA_TRATADOS", AQUI / "tratados"))
DB_URL = os.getenv("DB_URL", "postgresql://randuin:randuin_local@localhost:5432/randuin")

# En este orden: transacciones apunta a usuarios.
TABLAS = ["usuarios", "dispositivos", "lista_negra", "transacciones"]


def conectar():
    """La base ya esta sana (healthcheck del compose), pero se reintenta unos segundos por si acaso."""
    for intento in range(1, 11):
        try:
            return psycopg.connect(DB_URL)
        except psycopg.OperationalError as error:
            print(f"[carga-datos] la base no responde ({intento}/10): {error}", flush=True)
            time.sleep(2)
    sys.exit("[carga-datos] no se pudo conectar a la base")


def sql_insercion(tabla, columnas):
    """INSERT con ON CONFLICT DO NOTHING: si la fila ya existe (misma clave), se salta."""
    return (f"INSERT INTO {tabla} ({', '.join(columnas)}) VALUES ({', '.join(['%s'] * len(columnas))}) "
            "ON CONFLICT DO NOTHING")


def contar(cur, tabla):
    cur.execute(f"SELECT count(*) FROM {tabla}")
    return cur.fetchone()[0]


def cargar_tabla(cur, tabla):
    with (TRATADOS / f"{tabla}.csv").open(encoding="utf-8") as archivo:
        lector = csv.reader(archivo)
        columnas = next(lector)
        filas = [[valor if valor != "" else None for valor in fila] for fila in lector]   # vacio -> NULL
    antes = contar(cur, tabla)
    cur.executemany(sql_insercion(tabla, columnas), filas)
    total = contar(cur, tabla)
    print(f"[carga-datos] {tabla:<14} leidas={len(filas):>6}  nuevas={total - antes:>6}  total={total:>6}", flush=True)
    return {"leidas": len(filas), "nuevas": total - antes, "total": total}


def main():
    inicio = time.perf_counter()
    with conectar() as con, con.cursor() as cur:
        cur.execute((AQUI / "schema.sql").read_text(encoding="utf-8"))
        resultado = {tabla: cargar_tabla(cur, tabla) for tabla in TABLAS}
    # al salir del "with" se hace commit: todo o nada, nunca queda una carga a medias
    print(f"[carga-datos] terminada en {time.perf_counter() - inicio:.1f} s", flush=True)
    return resultado


if __name__ == "__main__":
    main()
