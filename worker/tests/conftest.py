import sys
from pathlib import Path

# Permite "from app.llm import ..." al correr pytest desde la raiz del repo o desde worker/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
