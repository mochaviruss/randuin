import sys
from pathlib import Path

# Permite "from app.main import ..." al correr pytest desde la raiz del repo o desde servicio-ia/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
