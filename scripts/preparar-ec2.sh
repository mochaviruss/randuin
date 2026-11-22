#!/usr/bin/env bash
# Prepara la EC2 la primera vez (EP3, etapa B). Se corre DENTRO de la EC2, como el usuario "ubuntu". Es idempotente: se puede repetir.
#
#   1. Instala Docker, Compose y Git.
#   2. Comprueba el rol de la instancia (LabInstanceProfile), tambien DESDE UN CONTENEDOR (eso exige hop limit 2), y que ese rol
#      llega al endpoint de SageMaker. Si esto falla, el servicio de IA daria NoCredentialsError.
#   3. Clona el repositorio.
#   4. Crea el .env con secretos al azar, el DOMINIO de sslip.io segun la IP elastica, y pide la clave de NVIDIA (opcional).
#
#   scp -i labsuser.pem scripts/preparar-ec2.sh ubuntu@<IP-ELASTICA>:~
#   ssh -i labsuser.pem ubuntu@<IP-ELASTICA>
#   bash preparar-ec2.sh                 # RAMA=dev bash preparar-ec2.sh  (antes de que main tenga el codigo nuevo)
#
# Nada de esto sube al repositorio: el .env queda solo en el servidor, con permisos 600.
set -euo pipefail

REPO="${REPO:-https://github.com/mochaviruss/randuin.git}"
RAMA="${RAMA:-main}"
TAG="${TAG:-latest}"                 # latest hasta que exista el tag ep3; despues, TAG=ep3
CARPETA="$HOME/randuin"
ENDPOINT="${ENDPOINT:-randuin-fraude}"

echo "### 1. Docker, Compose y Git"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -y
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 git curl openssl
  sudo systemctl enable --now docker
fi
sudo usermod -aG docker "$USER" || true      # se aplica al volver a entrar; en este script se usa sudo
sudo docker --version
sudo docker compose version

echo
echo "### 2. El rol de la instancia"
TOKEN="$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')"
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/iam/info | grep -E '"Code"|InstanceProfileArn' || {
  echo "FALLA: la instancia no tiene LabInstanceProfile (EC2 -> Actions -> Security -> Modify IAM role)." >&2; exit 1; }
IP="$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4)"
echo "IP publica de la instancia: $IP"

echo "Desde un contenedor (aqui se ve el hop limit; con 1 daria NoCredentialsError):"
sudo docker run --rm -e AWS_DEFAULT_REGION=us-east-1 -e ENDPOINT="$ENDPOINT" python:3.12-slim sh -c '
  pip install -q boto3 >/dev/null 2>&1 &&
  python - <<PY
import os, boto3
print("  rol:      ", boto3.client("sts").get_caller_identity()["Arn"])
print("  SageMaker:", boto3.client("sagemaker").describe_endpoint(EndpointName=os.environ["ENDPOINT"])["EndpointStatus"])
PY' || { echo "FALLA: el contenedor no obtiene el rol. Revisa Metadata response hop limit = 2 en la instancia." >&2; exit 1; }

echo
echo "### 3. El repositorio ($RAMA)"
if [ -d "$CARPETA/.git" ]; then
  git -C "$CARPETA" fetch origin && git -C "$CARPETA" checkout "$RAMA" && git -C "$CARPETA" pull origin "$RAMA"
else
  git clone --branch "$RAMA" "$REPO" "$CARPETA"
fi
cd "$CARPETA"

echo
echo "### 4. El .env"
poner() {      # poner CLAVE valor: reemplaza la linea si existe, o la agrega
  grep -v "^$1=" .env > .env.tmp || true
  printf '%s=%s\n' "$1" "$2" >> .env.tmp
  mv .env.tmp .env
}
if [ ! -f .env ]; then
  cp .env.example .env
  poner API_KEY "$(openssl rand -hex 16)"
  poner POSTGRES_PASSWORD "$(openssl rand -hex 12)"
  poner RABBITMQ_PASSWORD "$(openssl rand -hex 12)"
  echo ".env creado con secretos al azar."
else
  echo ".env ya existe: no se toca (se actualizan solo DOMINIO y TAG)."
fi
poner DOMINIO "$(echo "$IP" | tr . -).sslip.io"
poner TAG "$TAG"
if ! grep -Eq '^NVIDIA_API_KEY=.+' .env; then
  if [ -t 0 ]; then
    read -rsp "Clave de NVIDIA (no se muestra; Enter para dejarla vacia y usar el modo degradado): " CLAVE; echo
    [ -n "$CLAVE" ] && poner NVIDIA_API_KEY "$CLAVE"
  fi
fi
chmod 600 .env

echo
echo "Listo."
echo "  Dominio:  https://$(grep '^DOMINIO=' .env | cut -d= -f2)"
echo "  Imagenes: TAG=$(grep '^TAG=' .env | cut -d= -f2)"
echo "  La API_KEY quedo en .env (no se muestra aqui): grep ^API_KEY= $CARPETA/.env"
echo "Siguiente, cuando las imagenes esten publicadas:  cd $CARPETA && ./scripts/desplegar.sh"
echo "Si 'docker' pide sudo, sal y vuelve a entrar: el grupo docker se aplica al iniciar sesion."
