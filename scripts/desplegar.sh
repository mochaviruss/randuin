#!/usr/bin/env bash
# Levanta (o actualiza) Randuin en la EC2. Se corre DENTRO de la EC2, desde cualquier carpeta del repositorio clonado.
#
#   ./scripts/desplegar.sh            # imagenes :ep3 (la entrega)
#   ./scripts/desplegar.sh sha-1a2b3c4   # una version puntual de GHCR (para probar antes del tag, o volver atras)
#
# Antes (una vez):  cp .env.example .env   y completar API_KEY, POSTGRES_PASSWORD y DOMINIO (<ip-con-guiones>.sslip.io)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "Falta el .env. Corre:  cp .env.example .env   y completalo (API_KEY, POSTGRES_PASSWORD, DOMINIO)." >&2
  exit 1
fi
# Ni claves de acceso de AWS ni la clave de NVIDIA pueden estar en el repositorio; en el .env del servidor, tampoco las de AWS:
# el rol de la EC2 (LabInstanceProfile) las reemplaza (EP3, 2.2 regla 4).
if grep -Eiq '^(AWS_ACCESS|AWS_SECRET|AWS_SESSION)' .env; then
  echo "El .env tiene claves de acceso de AWS. Borralas: la EC2 usa su rol, no claves." >&2
  exit 1
fi

[ -n "${1:-}" ] && export TAG="$1"
echo "Imagenes: ${TAG:-$(grep -E '^TAG=' .env | cut -d= -f2- || true)}"

docker compose pull
docker compose up -d
echo
docker compose ps
echo
echo "Siguiente: ./scripts/verificar-nube.sh"
