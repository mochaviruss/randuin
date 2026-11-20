"""Tests de las rutas de EP3 del dominio, contra una base real con los datos cargados (TEST_DB_URL). El modelo se reemplaza por una
funcion falsa y la cola por una lista: aqui se prueba el dominio, no SageMaker ni RabbitMQ.

Cubre cuatro de las seis pruebas obligatorias de EP3 (4.3): 1 (200 con version de modelo), 2 (422 con detalle), 3 (401 sin clave)
y 6 (LLM caido: la prediccion responde igual y el resumen queda degradado). La 4 (429) esta en el gateway y la 5 (anonimizacion) en el worker.
"""
import os

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

DB = os.getenv("TEST_DB_URL")
pytestmark = pytest.mark.skipif(not DB, reason="sin base de prueba (TEST_DB_URL)")
CLAVE = {"X-API-Key": os.environ["API_KEY"]}


@pytest.fixture()
def api(monkeypatch):
    from app import main
    with psycopg.connect(DB) as con:
        if con.execute("SELECT count(*) FROM usuarios").fetchone()[0] == 0:
            pytest.skip("la base de prueba no tiene datos: corre antes la carga")
    publicados = []
    monkeypatch.setattr(main.cola, "publicar", lambda pedido: publicados.append(pedido))
    main._cache.clear()
    with TestClient(main.app) as c:
        yield c, main, publicados


def modelo_falso(score, version="test"):
    return lambda variables, tid: {"score": score, "modelo_version": version, "replica": "r1",
                                   "razones": [{"variable": "intentos_previos_1h",
                                                "condicion": "intentos del usuario en la ultima hora = 5 (sube el riesgo)"}]}


def un_usuario():
    with psycopg.connect(DB) as con:
        return con.execute("SELECT usuario_id FROM usuarios ORDER BY usuario_id LIMIT 1").fetchone()[0]


def entrada(**cambios):
    return {"usuario_id": un_usuario(), "monto": 25000, "canal": "web", **cambios}


def contar_predicciones():
    with psycopg.connect(DB) as con:
        return con.execute("SELECT count(*) FROM predicciones").fetchone()[0]


# ---------------------------------------------------------------------------------------------------------- prueba 1 de EP3
def test_predict_valido_responde_200_con_la_version_del_modelo_y_guarda(api, monkeypatch):
    c, main, publicados = api
    monkeypatch.setattr(main, "pedir_al_modelo", modelo_falso(0.95, version="randuin-fraude-PRUEBA"))
    r = c.post("/api/predict", json=entrada(), headers=CLAVE)
    assert r.status_code == 200
    d = r.json()
    assert d["decision"] == "bloquear" and d["origen"] == "modelo" and d["modelo_version"] == "randuin-fraude-PRUEBA"
    assert d["llm"]["estado"] == "pendiente" and d["consulta"] == f"/api/predicciones/{d['transaccion_id']}"
    guardada = c.get(d["consulta"], headers=CLAVE).json()      # entrada y resultado quedan guardados (RF7)
    assert guardada["decision"] == "bloquear" and guardada["modelo_version"] == "randuin-fraude-PRUEBA"
    assert guardada["entrada"]["monto"] == 25000 and guardada["razones"][0]["variable"] == "intentos_previos_1h"
    assert [p["id"] for p in publicados] == [d["transaccion_id"]]            # el pedido del resumen salio a la cola


# ---------------------------------------------------------------------------------------------------------- prueba 2 de EP3
def test_entrada_mal_formada_responde_422_con_el_detalle_y_no_guarda_nada(api):
    c, _, publicados = api
    antes = contar_predicciones()
    malas = [{"usuario_id": un_usuario()},                                   # falta el monto
             entrada(monto="mucho"),                                         # tipo incorrecto
             entrada(monto=-5), entrada(canal="fax"), entrada(pais_transaccion="Chile")]
    for mala in malas:
        r = c.post("/api/predict", json=mala, headers=CLAVE)
        assert r.status_code == 422, mala
        assert r.json()["detail"][0]["loc"], "el 422 debe decir que campo fallo"
    assert contar_predicciones() == antes and publicados == []


def test_el_numero_de_tarjeta_se_rechaza_no_entra_al_sistema(api):
    """RNF5 y RT4: la tarjeta no es un campo del contrato. Mandarla es un 422, no se guarda ni se loguea."""
    c, _, _ = api
    antes = contar_predicciones()
    r = c.post("/api/predict", json=entrada(tarjeta="4111111111111111"), headers=CLAVE)
    assert r.status_code == 422 and r.json()["detail"][0]["loc"][-1] == "tarjeta"
    assert r.json()["detail"][0]["type"] == "extra_forbidden"
    assert "4111111111111111" not in r.text                     # el 422 dice QUE campo sobra, pero no devuelve su valor
    assert contar_predicciones() == antes


# ---------------------------------------------------------------------------------------------------------- prueba 3 de EP3
def test_sin_clave_o_con_clave_equivocada_responde_401(api):
    c, _, _ = api
    rutas = [("post", "/api/predict", entrada()), ("get", "/api/predicciones/TX-X", None), ("get", "/api/revision", None),
             ("get", "/api/metricas", None)]
    for metodo, ruta, cuerpo in rutas:
        for cabeceras in ({}, {"X-API-Key": "clave-equivocada"}, {"X-API-Key": ""}):
            r = getattr(c, metodo)(ruta, headers=cabeceras, **({"json": cuerpo} if cuerpo else {}))
            assert r.status_code == 401, (ruta, cabeceras)


def test_sin_api_key_configurada_nadie_entra_falla_cerrada(api, monkeypatch):
    c, main, _ = api
    monkeypatch.setattr(main, "API_KEY", "")
    assert c.get("/api/revision", headers={"X-API-Key": ""}).status_code == 401
    assert c.get("/api/revision", headers=CLAVE).status_code == 401


def test_health_y_docs_son_publicos(api):
    c, main, _ = api
    assert c.get("/docs").status_code == 200 and c.get("/openapi.json").status_code == 200
    main._cache["sagemaker"] = (10**12, {"estado": "ok", "detalle": "x"})
    main._cache["cola"] = (10**12, (True, "ok"))
    assert c.get("/api/health").status_code == 200                           # sin clave


# ---------------------------------------------------------------------------------------------------------- prueba 6 de EP3
def test_con_el_llm_caido_la_prediccion_responde_igual_y_el_resumen_queda_degradado(api, monkeypatch):
    """El worker avisa 'degradado' cuando NVIDIA da 429 o esta caido. La decision que ya se respondio no cambia."""
    c, main, _ = api
    monkeypatch.setattr(main, "pedir_al_modelo", modelo_falso(0.95))
    d = c.post("/api/predict", json=entrada(), headers=CLAVE).json()
    assert d["decision"] == "bloquear" and d["llm"]["estado"] == "pendiente"
    r = c.put(f"/interno/predicciones/{d['transaccion_id']}/llm",
              json={"estado": "degradado", "motivo": "HTTP 429 despues de 4 intentos"})
    assert r.status_code == 200
    g = c.get(d["consulta"], headers=CLAVE).json()
    assert g["decision"] == "bloquear" and g["origen"] == "modelo"            # la decision no se toco
    assert g["llm"]["estado"] == "degradado" and g["llm"]["texto"] is None and "429" in g["llm"]["motivo"]


def test_si_rabbitmq_no_esta_la_prediccion_responde_igual_y_el_resumen_queda_degradado(api, monkeypatch):
    c, main, _ = api
    monkeypatch.setattr(main, "pedir_al_modelo", modelo_falso(0.95))

    def cola_caida(pedido):
        raise ConnectionError("RabbitMQ no responde")
    monkeypatch.setattr(main.cola, "publicar", cola_caida)
    r = c.post("/api/predict", json=entrada(), headers=CLAVE)
    assert r.status_code == 200 and r.json()["decision"] == "bloquear"
    g = c.get(r.json()["consulta"], headers=CLAVE).json()
    assert g["llm"]["estado"] == "degradado" and "cola no disponible" in g["llm"]["motivo"]


def test_el_resumen_listo_aparece_en_la_consulta_y_en_la_cola_de_revision(api, monkeypatch):
    c, main, _ = api
    monkeypatch.setattr(main, "pedir_al_modelo", modelo_falso(0.95))
    d = c.post("/api/predict", json=entrada(moneda="USD", monto=900_000), headers=CLAVE).json()
    c.put(f"/interno/predicciones/{d['transaccion_id']}/llm",
          json={"estado": "listo", "texto": "Se bloqueo por intentos repetidos.", "latencia_ms": 640})
    assert c.get(d["consulta"], headers=CLAVE).json()["llm"] == {"estado": "listo", "texto": "Se bloqueo por intentos repetidos.",
                                                                  "motivo": None, "latencia_ms": 640}
    en_cola = {f["transaccion_id"]: f for f in c.get("/api/revision?limite=100&horas=1", headers=CLAVE).json()["cola"]}
    assert en_cola[d["transaccion_id"]]["llm"]["texto"] == "Se bloqueo por intentos repetidos."


# --------------------------------------------------------------------------------------------------------- el pedido al LLM
def test_una_compra_aprobada_no_se_encola_ni_lleva_resumen(api, monkeypatch):
    c, main, publicados = api
    monkeypatch.setattr(main, "pedir_al_modelo", modelo_falso(0.05))
    d = c.post("/api/predict", json=entrada(), headers=CLAVE).json()
    assert d["decision"] == "aprobar" and d["llm"]["estado"] == "no_aplica"
    assert publicados == []


def test_el_pedido_al_llm_no_lleva_identidades_ni_la_probabilidad_cruda(api, monkeypatch):
    c, main, publicados = api
    monkeypatch.setattr(main, "pedir_al_modelo", modelo_falso(0.95))
    ejemplo = c.get("/ejemplos").json()["normal"]
    cuerpo = entrada(dispositivo_id=ejemplo["dispositivo_id"], comercio_id=ejemplo["comercio_id"], usuario_id=ejemplo["usuario_id"])
    c.post("/api/predict", json=cuerpo, headers=CLAVE)
    texto = publicados[0]["contenido"]
    assert "bloquear" in texto and "Nivel de riesgo: alto" in texto and "intentos del usuario en la ultima hora = 5" in texto
    for identidad in (ejemplo["usuario_id"], ejemplo["dispositivo_id"], ejemplo["comercio_id"]):
        assert identidad not in texto and identidad not in publicados[0]["instruccion"]
    assert "0.95" not in texto and "0,95" not in texto and "95" not in texto.replace("1h", "")


def test_la_lista_negra_bloquea_sin_modelo_y_pide_resumen(api, monkeypatch):
    c, main, publicados = api
    monkeypatch.setattr(main, "pedir_al_modelo", lambda *a: pytest.fail("no debia llamar al modelo"))
    bloqueado = c.get("/ejemplos").json()["lista_negra"]["dispositivo_id"]
    d = c.post("/api/predict", json=entrada(dispositivo_id=bloqueado), headers=CLAVE).json()
    assert d["decision"] == "bloquear" and d["origen"] == "lista_negra" and d["llm"]["estado"] == "pendiente"
    assert "regla dura" in publicados[0]["contenido"]


def test_con_el_modelo_caido_se_aplica_la_reserva_y_la_api_sigue_respondiendo(api, monkeypatch):
    c, main, _ = api

    def caido(variables, tid):
        raise httpx.ConnectTimeout("sin respuesta")
    monkeypatch.setattr(main, "pedir_al_modelo", caido)
    d = c.post("/api/predict", json=entrada(), headers=CLAVE).json()
    assert d["origen"] == "reserva" and d["score"] is None and d["modelo_version"] is None


# ------------------------------------------------------------------------------------------------------------ cola de revision
def test_la_cola_de_revision_se_ordena_por_riesgo_por_monto(api, monkeypatch):
    c, main, _ = api
    ids = {}
    # (score, monto USD): prioridad = score x monto -> B = 400.000, A = 270.000, C = 95.000
    for nombre, score, monto in (("A", 0.90, 300_000), ("B", 0.50, 800_000), ("C", 0.95, 100_000)):
        monkeypatch.setattr(main, "pedir_al_modelo", modelo_falso(score))
        ids[nombre] = c.post("/api/predict", json=entrada(moneda="USD", monto=monto), headers=CLAVE).json()["transaccion_id"]
    cola_ = c.get("/api/revision?limite=100&horas=1", headers=CLAVE).json()
    mias = [f for f in cola_["cola"] if f["transaccion_id"] in ids.values()]
    assert [f["transaccion_id"] for f in mias] == [ids["B"], ids["A"], ids["C"]]
    assert [float(f["prioridad"]) for f in mias] == [400_000.0, 270_000.0, 95_000.0]
    solo_revisar = c.get("/api/revision?decision=revisar&limite=100&horas=1", headers=CLAVE).json()["cola"]
    assert all(f["decision"] == "revisar" for f in solo_revisar) and ids["B"] in [f["transaccion_id"] for f in solo_revisar]
    assert c.get("/api/revision?limite=1", headers=CLAVE).json()["total"] == 1


# --------------------------------------------------------------------------------------------------------------- ruta interna
def test_ruta_interna_del_worker_valida_y_no_aparece_en_la_documentacion(api, monkeypatch):
    c, main, _ = api
    monkeypatch.setattr(main, "pedir_al_modelo", modelo_falso(0.95))
    tid = c.post("/api/predict", json=entrada(), headers=CLAVE).json()["transaccion_id"]
    assert c.get(f"/interno/predicciones/{tid}/llm").json() == {"estado": "pendiente"}
    assert c.get("/interno/predicciones/TX-NO-EXISTE/llm").status_code == 404
    assert c.put("/interno/predicciones/TX-NO-EXISTE/llm", json={"estado": "listo", "texto": "x"}).status_code == 404
    assert c.put(f"/interno/predicciones/{tid}/llm", json={"estado": "listo"}).status_code == 422          # listo sin texto
    assert c.put(f"/interno/predicciones/{tid}/llm", json={"estado": "listo", "texto": "x" * 4001}).status_code == 422
    assert c.put(f"/interno/predicciones/{tid}/llm", json={"estado": "invento"}).status_code == 422
    assert not [ruta for ruta in c.get("/openapi.json").json()["paths"] if ruta.startswith("/interno")]


# --------------------------------------------------------------------------------------------------------------- salud y metricas
def test_health_muestra_cada_pieza_por_separado_y_no_cae_si_falla_la_cola(api, monkeypatch):
    c, main, _ = api
    main._cache["sagemaker"] = (10**12, {"estado": "ok", "detalle": "endpoint randuin-fraude: InService"})
    main._cache["cola"] = (10**12, (False, "RabbitMQ no responde (AMQPConnectionError)"))
    r = c.get("/api/health")
    assert r.status_code == 200                                              # que la cola falle no tumba la API
    cuerpo = r.json()
    assert set(cuerpo["piezas"]) == {"base", "sagemaker", "cola", "llm"}
    assert cuerpo["piezas"]["base"]["estado"] == "ok" and cuerpo["piezas"]["cola"]["estado"] == "error"
    assert cuerpo["estado"] == "degradado"


def test_health_todo_bien_y_base_caida(api, monkeypatch):
    c, main, _ = api
    main._cache["sagemaker"] = (10**12, {"estado": "ok", "detalle": "x"})
    main._cache["cola"] = (10**12, (True, "RabbitMQ responde"))
    assert c.get("/api/health").json()["estado"] in ("ok", "degradado")
    monkeypatch.setattr(main, "_pieza_base", lambda: {"estado": "error", "detalle": "la base no responde"})
    r = c.get("/api/health")
    assert r.status_code == 503 and r.json()["estado"] == "caido"


def test_metricas_para_el_dashboard(api, monkeypatch):
    c, main, _ = api
    monkeypatch.setattr(main, "pedir_al_modelo", modelo_falso(0.95))
    c.post("/api/predict", json=entrada(), headers=CLAVE)
    m = c.get("/api/metricas?horas=1", headers=CLAVE).json()
    assert m["evaluadas"] >= 1 and m["por_decision"].get("bloquear", 0) >= 1 and m["por_origen"].get("modelo", 0) >= 1
    assert isinstance(m["pct_reserva"], float) and set(m["latencia_ms"]) == {"p50", "p95", "p99"}
    assert m["bloqueo_por_dia"] and {"dia", "evaluadas", "bloqueadas", "tasa_bloqueo_pct"} <= set(m["bloqueo_por_dia"][-1])
    assert m["en_cola_de_revision"] >= 1 and sum(m["resumenes_llm"].values()) >= 1


def test_la_documentacion_openapi_describe_las_rutas_del_enunciado(api):
    c, _, _ = api
    esquema = c.get("/openapi.json").json()
    for ruta in ("/api/predict", "/api/predicciones/{transaccion_id}", "/api/revision", "/api/health", "/api/metricas"):
        assert ruta in esquema["paths"], ruta
    assert any(s.get("name") == "X-API-Key" for s in esquema["components"]["securitySchemes"].values())
    assert esquema["paths"]["/api/predict"]["post"]["responses"]["422"]
