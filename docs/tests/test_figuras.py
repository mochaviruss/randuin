"""La figura 5.2 (contenedores) y el compose.yaml tienen que tener exactamente los mismos servicios
(enunciado EP2, 3.3). Este test lo comprueba en cada pull request: si alguien agrega un servicio al
compose y no a la figura, o al reves, el pipeline queda en rojo."""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]


def servicios_compose():
    """Nombres de los servicios; el guion se compara como guion bajo (Mermaid no acepta guion en el alias)."""
    texto = (RAIZ / "compose.yaml").read_text(encoding="utf-8")
    bloque = texto.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    return {n.replace("-", "_") for n in re.findall(r"^  ([a-z0-9_-]+):\s*$", bloque, flags=re.M)}


def contenedores_figura():
    """Nodos de la figura 4 con la clase cont (los contenedores de Randuin, notacion C4 en un flowchart)."""
    texto = (RAIZ / "docs" / "arquitectura" / "04-contenedores.mmd").read_text(encoding="utf-8")
    return set(re.findall(r"^\s*(\w+)[\[\(].*:::cont\s*$", texto, flags=re.M))


def test_figura_52_y_compose_tienen_los_mismos_servicios():
    compose, figura = servicios_compose(), contenedores_figura()
    assert compose == figura, f"solo en compose: {compose - figura} | solo en la figura: {figura - compose}"


def test_compose_sin_build_ni_puertos_extra():
    """Reglas 1 y 4 de la seccion 2.3: ningun build: y solo el gateway publica puerto."""
    texto = (RAIZ / "compose.yaml").read_text(encoding="utf-8")
    assert not re.search(r"^\s+build:", texto, flags=re.M), "el compose de la defensa no puede tener build:"
    assert texto.count("ports:") == 1 and '"8080:8080"' in texto, "solo el gateway publica el 8080"
    for imagen in re.findall(r"^\s+image:\s*(\S+)", texto, flags=re.M):
        assert imagen.endswith(":ep2") or imagen.startswith("postgres:"), f"imagen sin tag ep2: {imagen}"
