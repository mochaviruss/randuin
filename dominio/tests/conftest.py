import os
import sys
from pathlib import Path

# Permite "from app.main import ..." al correr pytest desde la raiz del repo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Los tests usan la base de prueba (la pone el pipeline en TEST_DB_URL).
if os.getenv("TEST_DB_URL"):
    os.environ["DB_URL"] = os.environ["TEST_DB_URL"]
