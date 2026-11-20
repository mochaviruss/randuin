import os
import sys
from pathlib import Path

# Permite "from app.main import ..." al correr pytest desde la raiz del repo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Los tests usan la base de prueba (la pone el pipeline en TEST_DB_URL).
if os.getenv("TEST_DB_URL"):
    os.environ["DB_URL"] = os.environ["TEST_DB_URL"]
# La API no trae clave por defecto (sin ella nadie entra): los tests usan esta, y la cola no existe (RABBITMQ_URL vacio).
os.environ.setdefault("API_KEY", "clave-de-prueba")
os.environ.pop("RABBITMQ_URL", None)
