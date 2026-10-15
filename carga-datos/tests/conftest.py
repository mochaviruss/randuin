import sys
from pathlib import Path

# Permite "import cargar" al correr pytest desde la raiz del repo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
