"""
Tratamiento de los datos del caso 3 (Kipu Pagos) para Randuin.

Se ejecuta desde la raiz del repositorio:   python datos/preparar.py
Despues se entrena con:                     python servicio-ia/entrenar.py

Hace, en orden:
  0. Validacion  el esquema de los archivos crudos es el esperado (paso 3 del pipeline de la EP1, cap. 7.4).
  1. Perfilado   filas, columnas, vacios y duplicados de cada archivo, antes y despues.
  2. Limpieza    una regla por cada problema encontrado; cada regla dice si corrige, descarta o marca.
  3. Integracion transacciones con usuarios y dispositivos; informa las que quedan sin pareja.
  4. Variables   las entradas del modelo, de que columna sale cada una.
  5. Salidas     - carga-datos/tratados/*.csv: las tablas tratadas que el servicio de carga sube a la base.
                 - datos/salida/etiquetadas.csv: las transacciones que un analista reviso (las unicas con
                   etiqueta), con sus variables y el conjunto al que pertenecen: "prueba" es el conjunto
                   FIJO (datos/conjunto_prueba.csv, RF11) y "entrenamiento" el resto. Es la entrada de
                   servicio-ia/entrenar.py, que entrena la red, la exporta a ONNX y aplica la compuerta.
                 - datos/salida/reporte.md y reporte.json: el antes y despues (capitulo 8.4 del informe).
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
CRUDOS = RAIZ / "datos" / "crudos"
SALIDA_CARGA = RAIZ / "carga-datos" / "tratados"
SALIDA_REPORTE = RAIZ / "datos" / "salida"

# Tipo de cambio fijo para llevar todo a USD. Sale del caso: USD 26 = 24.700 CLP.
CLP_POR_USD = 950

ARCHIVO_PRUEBA = RAIZ / "datos" / "conjunto_prueba.csv"     # conjunto de prueba FIJO (RF11), va en el repo

# Columnas que el tratamiento necesita en cada archivo crudo.
ESQUEMA = {
    "usuarios": ["usuario_id", "fecha_registro", "pais", "segmento", "nivel_kyc", "verificado",
                 "transacciones_previas", "email_dominio", "telefono_verificado"],
    "dispositivos": ["dispositivo_id", "tipo", "sistema_operativo", "primera_vez", "usuarios_distintos",
                     "emulador", "pais_declarado"],
    "lista_negra": ["entrada_id", "tipo", "valor", "fecha_alta", "motivo", "agregado_por", "vigente"],
    "transacciones": ["transaccion_id", "usuario_id", "comercio_id", "dispositivo_id", "timestamp", "monto",
                      "moneda", "canal", "mcc", "pais_transaccion", "latitud", "longitud", "ip",
                      "tarjeta_enmascarada", "resultado_autorizacion", "intentos_previos_1h", "es_fraude"],
}

# Variables del modelo (las mismas 7 que recibe POST /predict). Todas existen al momento de decidir:
# ninguna depende de la revision del analista. El escalado y el one-hot del canal los hace el
# preprocesador de servicio-ia/entrenar.py (preprocesador.joblib), no este script.
VARIABLES = [
    "monto_usd",                      # transacciones.monto + moneda, convertido a USD
    "hora_local",                     # transacciones.timestamp, en hora de Chile
    "intentos_previos_1h",            # transacciones.intentos_previos_1h (velocidad, RF10)
    "dispositivo_usuarios_distintos", # dispositivos.usuarios_distintos (-1 si no hay dispositivo)
    "sin_dispositivo",                # 1 si la transaccion llego sin dispositivo_id
    "pais_extranjero",                # 1 si transacciones.pais_transaccion no es CL
    "canal",                          # canal normalizado: app, web, pos o api
]

PAISES = {
    "CL": "CL", "CHILE": "CL", "PE": "PE", "PERU": "PE", "PERÚ": "PE", "AR": "AR", "ARGENTINA": "AR",
    "BR": "BR", "BRASIL": "BR", "BRAZIL": "BR", "CO": "CO", "COLOMBIA": "CO", "US": "US", "USA": "US",
    "EEUU": "US", "ESTADOS UNIDOS": "US", "VE": "VE", "VENEZUELA": "VE", "RU": "RU", "RUSIA": "RU",
    "RUSSIA": "RU", "NG": "NG", "NIGERIA": "NG", "MX": "MX", "MEXICO": "MX", "MÉXICO": "MX",
}

bitacora = []  # cada regla aplicada: archivo, problema, regla, accion, filas afectadas


def regla(archivo, problema, accion, filas, razon):
    bitacora.append({"archivo": archivo, "problema": problema, "accion": accion,
                     "filas": int(filas), "razon": razon})


def leer(nombre):
    # Todo como texto: asi se ve la suciedad tal cual viene y nada se convierte en silencio.
    return pd.read_csv(CRUDOS / f"{nombre}.csv", dtype=str, keep_default_na=False)


def tipo_legible(serie):
    t = str(serie.dtype)
    return {"object": "texto", "string": "texto", "bool": "booleano", "boolean": "booleano",
            "int64": "entero", "Int64": "entero", "float64": "decimal"}.get(t, "fecha" if "datetime" in t else t)


def perfilar(df, nombre, tipos=None):
    """Filas, columnas, tipos, nulos y duplicados (enunciado EP2, 2.2 paso 1).
    tipos: los que pandas infiere al leer el CSV crudo sin ayuda (antes) o los reales (despues)."""
    vacios = {c: int((df[c].astype(str).str.strip().isin(["", "nan", "NaT", "None", "<NA>"])).sum())
              for c in df.columns}
    return {"archivo": nombre, "filas": len(df), "columnas": len(df.columns),
            "duplicados_exactos": int(df.astype(str).duplicated().sum()),
            "tipos": tipos or {c: tipo_legible(df[c]) for c in df.columns},
            "vacios_por_columna": vacios}


def tipos_inferidos(nombre):
    """Como queda cada columna si se lee el CSV crudo tal cual: un monto en 'texto' delata formatos mezclados."""
    crudo = pd.read_csv(CRUDOS / f"{nombre}.csv", low_memory=False)
    return {c: tipo_legible(crudo[c]) for c in crudo.columns}


def vacio(serie):
    return serie.astype(str).str.strip() == ""


def normalizar_pais(serie):
    return serie.str.strip().str.upper().map(PAISES)


def a_booleano(serie):
    mapa = {"1": True, "SI": True, "TRUE": True, "0": False, "NO": False, "FALSE": False}
    return serie.str.strip().str.upper().map(mapa).astype("boolean")  # vacio -> <NA> = desconocido


def fecha_a_utc(texto):
    """Fechas con hora. Hay cuatro formatos y dos zonas horarias:
       ...Z               -> ya viene en UTC
       ...-04:00          -> trae su zona; se convierte a UTC
       YYYY-MM-DD HH:MM:SS y DD/MM/YYYY HH:MM sin zona -> hora de Chile (su distribucion por
       hora calza con la de las -04:00 y no con las Z), se convierte a UTC."""
    s = texto.strip()
    try:
        if s.endswith("Z") or re.search(r"[+-]\d\d:\d\d$", s):
            return pd.Timestamp(s).tz_convert("UTC")
        if "/" in s:
            local = pd.to_datetime(s, format="%d/%m/%Y %H:%M")
        else:
            local = pd.Timestamp(s)
        return local.tz_localize("America/Santiago", ambiguous=True,
                                 nonexistent="shift_forward").tz_convert("UTC")
    except (ValueError, TypeError):
        return pd.NaT


def fecha_simple(texto):
    """Fechas sin hora en tres formatos: DD/MM/YYYY, DD-MM-YYYY y YYYY-MM-DD."""
    s = texto.strip()
    for formato in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, formato).date()
        except ValueError:
            continue
    return None


def monto_a_usd(monto, moneda):
    """Montos en CLP y USD mezclados. Reglas, comprobadas contra la columna moneda:
       '$' se ignora (en Chile tambien es el signo del peso).
       '1.234' o '12.345.678' (punto cada 3 cifras) -> CLP con separador de miles.
       '12.34' o '12,34' (1 o 2 decimales)          -> USD.
       entero con moneda informada                   -> esa moneda.
       entero sin moneda: >= 1.000 -> CLP; menor -> USD (el CLP entero mas bajo es 901 y
       el USD entero mas alto es 301)."""
    s = monto.strip().replace("$", "")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        return float(s.replace(".", "")) / CLP_POR_USD, "CLP", "punto de miles -> CLP"
    if re.fullmatch(r"\d+[.,]\d{1,2}", s):
        return float(s.replace(",", ".")), "USD", "1 o 2 decimales (punto o coma) -> USD"
    if re.fullmatch(r"-?\d+", s):
        valor = float(s)
        mon = moneda.strip().upper()
        if mon in ("CLP", "USD"):
            caso = f"entero con moneda {mon}"
        else:
            mon = "CLP" if abs(valor) >= 1000 else "USD"
            caso = f"entero sin moneda, {'>= 1.000 -> CLP' if mon == 'CLP' else '< 1.000 -> USD'}"
        return (valor / CLP_POR_USD if mon == "CLP" else valor), mon, caso
    return None, None, "formato no reconocido"


# ---------------------------------------------------------------------------- usuarios
def limpiar_usuarios(df):
    n = len(df)
    df = df.drop_duplicates()
    regla("usuarios", "filas repetidas exactas", "descarta", n - len(df),
          "es el mismo usuario registrado dos veces; se deja una copia")

    df = df.copy()
    df["fecha_registro"] = pd.to_datetime(df["fecha_registro"].map(fecha_simple))
    regla("usuarios", "fecha_registro en 3 formatos (DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD)",
          "corrige", n, "se lleva todo a fecha ISO")

    pais = normalizar_pais(df["pais"])
    regla("usuarios", "pais escrito de 31 formas ('CL', ' cl', 'Chile', 'CHILE'...)", "corrige",
          int((pais != df["pais"]).sum()), "se lleva al codigo ISO de 2 letras")
    df["pais"] = pais

    seg = (df["segmento"].str.strip().str.lower()
           .str.replace(" ", "_").str.replace("ñ", "n")
           .replace({"individuo": "persona", "pyme": "comercio_pequeno"}))
    regla("usuarios", "segmento con mayusculas, espacios, enie y sinonimos (individuo, pyme, 'comercio pequeño')",
          "corrige", int((seg != df["segmento"]).sum()),
          "individuo es persona y pyme es comercio pequeno; 'empresa' (16 filas) se deja como categoria propia")
    df["segmento"] = seg

    kyc = pd.to_numeric(df["nivel_kyc"], errors="coerce")
    fuera = int((kyc < 0).sum())
    kyc = kyc.where(kyc >= 0)
    regla("usuarios", "nivel_kyc -1 (fuera del rango 0-3) o vacio", "marca como desconocido",
          fuera + int(vacio(df["nivel_kyc"]).sum()), "no se inventa un nivel de verificacion")
    df["nivel_kyc"] = kyc.astype("Int64")

    df["verificado"] = a_booleano(df["verificado"])
    df["telefono_verificado"] = a_booleano(df["telefono_verificado"])
    regla("usuarios", "verificado como 1/0/si/no y vacios", "corrige", n,
          "se lleva a verdadero/falso; vacio queda desconocido, no falso")

    df["transacciones_previas"] = pd.to_numeric(df["transacciones_previas"], errors="coerce").astype("Int64")
    df["email_dominio"] = df["email_dominio"].str.strip().str.lower().replace("", None)
    df["email_desechable"] = df["email_dominio"].isin(["tempmail.io", "mailinator.com"])

    # edad_cuenta_dias viene negativa en 117 filas: no se usa; se recalcula desde fecha_registro.
    regla("usuarios", "edad_cuenta_dias negativa", "descarta la columna",
          int((pd.to_numeric(df["edad_cuenta_dias"], errors="coerce") < 0).sum()),
          "se recalcula siempre desde fecha_registro, que es el dato de origen")
    return df.drop(columns=["edad_cuenta_dias"])


# ------------------------------------------------------------------------- dispositivos
def limpiar_dispositivos(df):
    n = len(df)
    df = df.copy()
    tipo = df["tipo"].str.strip().str.lower()
    regla("dispositivos", "tipo con mayusculas (ANDROID, Ios...)", "corrige",
          int((tipo != df["tipo"]).sum()), "android, ios, web o pos")
    df["tipo"] = tipo
    df["sistema_operativo"] = df["sistema_operativo"].str.strip().replace("", None)
    df["primera_vez"] = df["primera_vez"].map(fecha_a_utc)
    regla("dispositivos", "primera_vez en 4 formatos y 2 zonas horarias", "corrige", n, "se lleva a UTC")
    df["usuarios_distintos"] = pd.to_numeric(df["usuarios_distintos"], errors="coerce").astype("Int64")
    df["emulador"] = a_booleano(df["emulador"])
    regla("dispositivos", "emulador vacio en 4.933 filas", "marca como desconocido",
          int(df["emulador"].isna().sum()), "vacio no significa que no sea emulador")
    df["pais_declarado"] = normalizar_pais(df["pais_declarado"])
    return df


# --------------------------------------------------------------------------- lista negra
def limpiar_lista_negra(df):
    df = df.copy()
    tipo = df["tipo"].str.strip().str.lower().replace(
        {"device": "dispositivo", "merchant": "comercio", "card": "tarjeta"})
    regla("lista_negra", "tipo en ingles y con mayusculas (device, merchant, card, DISPOSITIVO, Ip)", "corrige",
          int((tipo != df["tipo"]).sum()), "dispositivo, ip, comercio o tarjeta")
    df["tipo"] = tipo
    valor = df["valor"].str.strip()
    regla("lista_negra", "valor con espacios alrededor (' ****7801 ')", "corrige",
          int((valor != df["valor"]).sum()), "si no, la comparacion con la transaccion falla")
    df["valor"] = valor
    df["fecha_alta"] = pd.to_datetime(df["fecha_alta"].map(fecha_simple))
    df["motivo"] = df["motivo"].str.strip().replace("", "sin_motivo")
    df["agregado_por"] = df["agregado_por"].str.strip().replace("", "desconocido")
    repetidas = df.duplicated(["tipo", "valor"])
    regla("lista_negra", "mismo valor bloqueado dos veces (comercio CM40825)", "descarta",
          int(repetidas.sum()), "se deja la primera entrada, que trae el motivo")
    df = df[~repetidas]
    sin_vigencia = int(vacio(df["vigente"]).sum())
    df["vigente"] = a_booleano(df["vigente"]).fillna(True)
    regla("lista_negra", "vigente vacio", "corrige a vigente", sin_vigencia,
          "la lista negra es una regla dura (RF4): una entrada sale solo si cumplimiento la da de baja")
    return df


# ------------------------------------------------------------------------- transacciones
def limpiar_transacciones(df, usuarios):
    archivo = "transacciones"
    n = len(df)
    ids_repetidos = int(df["transaccion_id"].duplicated().sum())
    filas_con_id_repetido = int(df["transaccion_id"].duplicated(keep=False).sum())
    df = df.drop_duplicates()
    regla(archivo, f"duplicados del sistema: {filas_con_id_repetido} filas comparten {ids_repetidos} transaccion_id "
                   "y son identicas en todas las columnas (incluida la hora)", "descarta",
          n - len(df), f"es una sola transaccion registrada dos veces: queda 1 fila por id "
                       f"(antes {filas_con_id_repetido} filas con id repetido, despues 0)")
    df = df.copy()

    moneda_vacia = int(vacio(df["moneda"]).sum())
    conv = df.apply(lambda f: monto_a_usd(f["monto"], f["moneda"]), axis=1)
    df["monto_usd"] = [c[0] for c in conv]
    df["moneda"] = [c[1] for c in conv]
    casos = pd.Series([c[2] for c in conv]).value_counts()
    regla(archivo, "montos en CLP y USD mezclados, con '$', puntos de miles y comas decimales",
          "corrige", len(df), f"se infiere la moneda del formato y se convierte a USD ({CLP_POR_USD} CLP = 1 USD): "
          + "; ".join(f"{k}: {v}" for k, v in casos.items()))
    regla(archivo, "moneda vacia", "corrige", moneda_vacia,
          "se infiere del formato del monto (ver monto_a_usd)")
    malo = df["monto_usd"].isna() | (df["monto_usd"] <= 0)
    regla(archivo, "monto cero o negativo", "descarta", int(malo.sum()),
          "no es una compra que evaluar: son reversos o validaciones de tarjeta")
    df = df[~malo]

    df["ts_utc"] = df["timestamp"].map(fecha_a_utc)
    regla(archivo, "timestamp en 4 formatos, unas en UTC y otras en hora de Chile", "corrige", len(df),
          "todo a UTC; las fechas sin zona son hora de Chile")
    malo = df["ts_utc"].isna()
    regla(archivo, "timestamp que no se puede leer", "descarta", int(malo.sum()), "sin fecha no hay hora ni velocidad")
    df = df[~malo]

    sin_usuario = ~df["usuario_id"].isin(usuarios["usuario_id"])
    regla(archivo, "usuario_id que no existe en usuarios (KP9xxxxx)", "descarta", int(sin_usuario.sum()),
          "sin pareja en el maestro de usuarios: en produccion es un error de integracion que se investiga")
    df = df[~sin_usuario]

    escrituras = df["canal"].nunique()
    canal = df["canal"].str.strip().str.lower().replace({"movil": "app", "punto_venta": "pos"})
    regla(archivo, f"canal escrito de {escrituras} formas (APP, Web, movil, punto_venta...)", "corrige",
          int((canal != df["canal"]).sum()), "app, web, pos o api")
    df["canal"] = canal

    pais = normalizar_pais(df["pais_transaccion"])
    regla(archivo, "pais_transaccion escrito de 31 formas", "corrige",
          int((pais != df["pais_transaccion"]).sum()), "codigo ISO de 2 letras")
    df["pais_transaccion"] = pais

    lat = pd.to_numeric(df["latitud"], errors="coerce")
    lon = pd.to_numeric(df["longitud"], errors="coerce")
    cero = (lat == 0) & (lon == 0)
    regla(archivo, "coordenadas (0, 0), en el Golfo de Guinea", "marca como desconocido",
          int(cero.sum()), "es el valor por defecto de un GPS sin senal, no una ubicacion")
    df["latitud"] = lat.where(~cero)
    df["longitud"] = lon.where(~cero)

    ip = df["ip"].str.strip()
    valida = ip.str.fullmatch(r"(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}") & (ip != "0.0.0.0")
    regla(archivo, "ip invalida ('-', 'n/a', '0.0.0.0', '999.1.1.1', vacia)", "marca como desconocido",
          int((~valida).sum()), "no se puede comparar con la lista negra")
    df["ip"] = ip.where(valida)

    for col in ["dispositivo_id", "comercio_id", "tarjeta_enmascarada"]:
        df[col] = df[col].str.strip().replace("", None)
    df["resultado_autorizacion"] = df["resultado_autorizacion"].str.strip().str.lower()
    df["intentos_previos_1h"] = pd.to_numeric(df["intentos_previos_1h"], errors="coerce").astype("Int64")

    etiqueta = df["es_fraude"].str.strip().str.lower().map({"1": 1, "si": 1, "0": 0, "no": 0})
    regla(archivo, "es_fraude como 1/0/si/no/SI", "corrige", int(etiqueta.notna().sum()),
          f"1 o 0: {int((etiqueta == 1).sum())} fraudes y {int((etiqueta == 0).sum())} legitimas revisadas")
    regla(archivo, "es_fraude vacia = transaccion que nadie reviso", "marca como sin etiqueta",
          int(etiqueta.isna().sum()), "vacio NO significa legitima: se excluyen del entrenamiento y de la prueba")
    df["es_fraude"] = etiqueta.astype("Int64")

    # Reintentos: id distinto, mismo usuario + comercio + monto, menos de 2 minutos despues.
    # No se borran (son intentos reales del cliente): se marcan.
    orden = df.sort_values(["usuario_id", "comercio_id", "monto", "ts_utc"])
    delta = orden.groupby(["usuario_id", "comercio_id", "monto"])["ts_utc"].diff().dt.total_seconds()
    reintento = (delta <= 120) & orden["comercio_id"].notna()
    df["es_reintento"] = reintento.reindex(df.index).fillna(False)
    regla(archivo, "reintentos legitimos del cliente (id distinto, mismo usuario/comercio/monto, a menos de 2 min)",
          "marca", int(df["es_reintento"].sum()),
          "son intentos reales del cliente: se conservan (antes y despues la misma cantidad) y se marcan es_reintento")

    # Fuga de informacion: estas columnas se llenan despues de la revision del analista.
    regla(archivo, "revisado_por_analista y motivo_bloqueo se llenan despues de conocer el resultado",
          "descarta las columnas", len(df), "con ellas el modelo aprende la respuesta y en produccion no sirve (R2)")
    # Minimizacion (RN5, RNF5): ip y tarjeta_enmascarada no son variables del modelo ni hacen falta para
    # decidir sobre el historial; no se cargan a la base. La lista negra si las conserva para comparar.
    regla(archivo, "ip y tarjeta_enmascarada (datos personales / de tarjeta)", "descarta las columnas", len(df),
          "no son variables del modelo ni se guardan en la base (Ley 19.628/21.719, RNF5)")
    return df.drop(columns=["revisado_por_analista", "motivo_bloqueo", "timestamp", "monto", "ip",
                            "tarjeta_enmascarada"])


# ------------------------------------------------------------------- integracion y modelo
def integrar(tx, usuarios, dispositivos, descartadas_sin_usuario):
    con_disp = tx["dispositivo_id"].notna()
    sin_pareja_disp = con_disp & ~tx["dispositivo_id"].isin(dispositivos["dispositivo_id"])
    datos = tx.merge(usuarios[["usuario_id", "pais"]], on="usuario_id", how="left")
    datos = datos.merge(dispositivos[["dispositivo_id", "usuarios_distintos"]], on="dispositivo_id", how="left")
    return datos, {
        "transacciones_sin_usuario (descartadas en la limpieza)": descartadas_sin_usuario,
        "transacciones_sin_dispositivo_id": int((~con_disp).sum()),
        "transacciones_con_dispositivo_inexistente": int(sin_pareja_disp.sum()),
    }


def calcular_variables(datos):
    """Las mismas reglas usa el microservicio de dominio para armar la entrada de /predict."""
    v = pd.DataFrame(index=datos.index)
    v["monto_usd"] = datos["monto_usd"].round(2)
    v["hora_local"] = datos["ts_utc"].dt.tz_convert("America/Santiago").dt.hour
    v["intentos_previos_1h"] = datos["intentos_previos_1h"].fillna(0).astype(int)
    v["dispositivo_usuarios_distintos"] = datos["usuarios_distintos"].fillna(-1).astype(int)
    v["sin_dispositivo"] = datos["dispositivo_id"].isna().astype(int)
    v["pais_extranjero"] = (datos["pais_transaccion"].fillna("CL") != "CL").astype(int)
    v["canal"] = datos["canal"]
    return v[VARIABLES]


def main():
    SALIDA_CARGA.mkdir(parents=True, exist_ok=True)
    SALIDA_REPORTE.mkdir(parents=True, exist_ok=True)

    crudos = {n: leer(n) for n in ["usuarios", "dispositivos", "lista_negra", "transacciones"]}
    validar_esquema(crudos)
    antes = {n: perfilar(d, n, tipos_inferidos(n)) for n, d in crudos.items()}

    usuarios = limpiar_usuarios(crudos["usuarios"])
    dispositivos = limpiar_dispositivos(crudos["dispositivos"])
    lista_negra = limpiar_lista_negra(crudos["lista_negra"])
    tx = limpiar_transacciones(crudos["transacciones"], usuarios)

    sin_usuario = next(b["filas"] for b in bitacora if b["problema"].startswith("usuario_id que no existe"))
    datos, integracion = integrar(tx, usuarios, dispositivos, sin_usuario)
    despues = {n: perfilar(d, n) for n, d in
               [("usuarios", usuarios), ("dispositivos", dispositivos), ("lista_negra", lista_negra), ("transacciones", tx)]}

    # --- entrada del entrenamiento: solo las transacciones que un analista reviso (las unicas con etiqueta)
    etiquetadas = datos[datos["es_fraude"].notna()].set_index("transaccion_id")
    ids_prueba = conjunto_prueba(etiquetadas)
    ent = calcular_variables(etiquetadas)
    ent.insert(0, "ts_utc", etiquetadas["ts_utc"].map(lambda x: x.isoformat()))
    ent["es_fraude"] = etiquetadas["es_fraude"].astype(int)
    ent["conjunto"] = ["prueba" if i in ids_prueba else "entrenamiento" for i in ent.index]
    ent = ent.reset_index().sort_values("ts_utc")
    ent.to_csv(SALIDA_REPORTE / "etiquetadas.csv", index=False)
    resumen_etiquetadas = {
        c: {"filas": int((ent["conjunto"] == c).sum()), "fraudes": int(ent.loc[ent["conjunto"] == c, "es_fraude"].sum())}
        for c in ["entrenamiento", "prueba"]}
    resumen_etiquetadas["sin_etiqueta_excluidas"] = int(datos["es_fraude"].isna().sum())

    # --- salidas para el servicio de carga
    usuarios.to_csv(SALIDA_CARGA / "usuarios.csv", index=False)
    d_out = dispositivos.copy()
    d_out["primera_vez"] = d_out["primera_vez"].map(lambda x: x.isoformat() if pd.notna(x) else "")
    d_out.to_csv(SALIDA_CARGA / "dispositivos.csv", index=False)
    lista_negra.to_csv(SALIDA_CARGA / "lista_negra.csv", index=False)
    t_out = tx.copy()
    t_out["ts_utc"] = t_out["ts_utc"].map(lambda x: x.isoformat())
    t_out["monto_usd"] = t_out["monto_usd"].round(2)
    columnas_tx = ["transaccion_id", "usuario_id", "comercio_id", "dispositivo_id", "ts_utc", "monto_usd", "moneda",
                   "canal", "mcc", "pais_transaccion", "latitud", "longitud",
                   "resultado_autorizacion", "intentos_previos_1h", "es_reintento", "es_fraude"]
    t_out[columnas_tx].to_csv(SALIDA_CARGA / "transacciones.csv", index=False)

    # --- reporte del tratamiento (capitulo 8.4 del documento)
    reporte = {"antes": antes, "despues": despues, "reglas": bitacora, "integracion": integracion,
               "variables": VARIABLES, "etiquetadas": resumen_etiquetadas}
    (SALIDA_REPORTE / "reporte.json").write_text(json.dumps(reporte, indent=2, ensure_ascii=False, default=str))
    escribir_reporte_md(reporte)

    print(f"usuarios {antes['usuarios']['filas']} -> {len(usuarios)} | dispositivos {len(dispositivos)} | "
          f"lista_negra {len(lista_negra)} | transacciones {antes['transacciones']['filas']} -> {len(tx)}")
    e, p = resumen_etiquetadas["entrenamiento"], resumen_etiquetadas["prueba"]
    print(f"etiquetadas: entrenamiento {e['filas']} ({e['fraudes']} fraudes) | prueba fija {p['filas']} "
          f"({p['fraudes']} fraudes) -> {SALIDA_REPORTE / 'etiquetadas.csv'}")
    print("OK: datos tratados. Siguiente paso: python servicio-ia/entrenar.py")


def validar_esquema(crudos):
    """Paso 3 del pipeline de la EP1: si un archivo no trae las columnas esperadas, no se entrena."""
    faltan = {n: sorted(set(cols) - set(crudos[n].columns)) for n, cols in ESQUEMA.items()}
    faltan = {n: f for n, f in faltan.items() if f}
    if faltan:
        sys.exit(f"ERROR: esquema distinto al esperado, faltan columnas {faltan}. No se entrena.")


def conjunto_prueba(etiquetadas):
    """El conjunto de prueba es FIJO (RF11) y se separa POR FECHA: el 25% mas reciente de lo etiquetado.
    Asi se prueba como en produccion (se entrena con el pasado y se evalua sobre lo que vino despues)
    y se evita que transacciones de una misma racha de fraude queden a ambos lados.
    La primera vez se guarda en el repo; desde ahi todos los candidatos se miden contra las mismas filas."""
    if ARCHIVO_PRUEBA.exists():
        ids = pd.read_csv(ARCHIVO_PRUEBA, dtype=str)["transaccion_id"]
        return set(ids[ids.isin(etiquetadas.index)])
    corte = etiquetadas["ts_utc"].quantile(0.75)
    ids = etiquetadas.index[etiquetadas["ts_utc"] > corte]
    pd.DataFrame({"transaccion_id": sorted(ids)}).to_csv(ARCHIVO_PRUEBA, index=False)
    print(f"AVISO: se creo {ARCHIVO_PRUEBA.name} con {len(ids)} transacciones posteriores a {corte:%Y-%m-%d}. "
          "Debe quedar en el repositorio.")
    return set(ids)


def escribir_reporte_md(r):
    lineas = ["# Reporte del tratamiento de datos\n", "Generado por `datos/preparar.py`.\n",
              "## Perfilado antes y despues\n", "| Archivo | Filas antes | Filas despues | Duplicados antes | Duplicados despues |",
              "|---|---|---|---|---|"]
    for n in r["antes"]:
        a, d = r["antes"][n], r["despues"][n]
        lineas.append(f"| {n} | {a['filas']} | {d['filas']} | {a['duplicados_exactos']} | {d['duplicados_exactos']} |")
    for n in r["antes"]:
        a, d = r["antes"][n], r["despues"][n]
        lineas += [f"\n### {n}: tipos y vacios por columna\n",
                   "| Columna | Tipo antes | Tipo despues | Vacios antes | Vacios despues |", "|---|---|---|---|---|"]
        for c in dict.fromkeys(list(a["tipos"]) + list(d["tipos"])):
            lineas.append(f"| {c} | {a['tipos'].get(c, '(no existia)')} | {d['tipos'].get(c, '(eliminada)')} | "
                          f"{a['vacios_por_columna'].get(c, '')} | {d['vacios_por_columna'].get(c, '')} |")
    lineas += ["\n## Reglas de limpieza\n", "| Archivo | Problema | Accion | Filas | Por que |", "|---|---|---|---|---|"]
    for b in r["reglas"]:
        lineas.append(f"| {b['archivo']} | {b['problema']} | {b['accion']} | {b['filas']} | {b['razon']} |")
    lineas += ["\n## Integracion\n"] + [f"- {k}: {v}" for k, v in r["integracion"].items()]
    lineas += ["\n## Variables del modelo\n", ", ".join(f"`{v}`" for v in r["variables"]),
               "\n## Transacciones etiquetadas (entrada de servicio-ia/entrenar.py)\n",
               "| Conjunto | Filas | Fraudes |", "|---|---|---|"]
    for c in ["entrenamiento", "prueba"]:
        lineas.append(f"| {c} | {r['etiquetadas'][c]['filas']} | {r['etiquetadas'][c]['fraudes']} |")
    lineas.append(f"\nSin etiqueta (excluidas del entrenamiento): {r['etiquetadas']['sin_etiqueta_excluidas']}")
    (SALIDA_REPORTE / "reporte.md").write_text("\n".join(lineas), encoding="utf-8")


if __name__ == "__main__":
    main()
