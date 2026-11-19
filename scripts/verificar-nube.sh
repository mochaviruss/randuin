#!/usr/bin/env bash
# Comprueba el sistema desplegado desde afuera, como lo haria el docente: HTTPS, la pagina, el estado de ia y de SageMaker,
# el 401 sin clave y una prediccion con clave.
#
#   ./scripts/verificar-nube.sh                                  # lee DOMINIO y API_KEY del .env
#   ./scripts/verificar-nube.sh 184-197-32-51.sslip.io mi-clave  # o se los pasas
#
# Sirve desde la EC2 o desde tu computador. Con DOMINIO=localhost (o localhost:8443) usa curl -k (certificado propio de Caddy).
set -uo pipefail
cd "$(dirname "$0")/.."

DOMINIO="${1:-$(grep -E '^DOMINIO=' .env 2>/dev/null | cut -d= -f2-)}"
API_KEY="${2:-$(grep -E '^API_KEY=' .env 2>/dev/null | cut -d= -f2-)}"
[ -n "$DOMINIO" ] && [ -n "$API_KEY" ] || { echo "Falta DOMINIO o API_KEY (pasalos como argumentos o ponlos en el .env)." >&2; exit 2; }
CURL=(curl -s --max-time 30)
case "$DOMINIO" in localhost*) CURL+=(-k) ;; esac        # localhost o localhost:8443: certificado propio de Caddy
BASE="https://$DOMINIO"
ENTRADA='{"monto_usd":52,"hora_local":3,"intentos_previos_1h":5,"dispositivo_usuarios_distintos":4,"sin_dispositivo":false,"pais_extranjero":true,"canal":"web"}'
fallas=0

comprobar() {   # comprobar "descripcion" codigo_esperado codigo_obtenido
  if [ "$2" = "$3" ]; then echo "  ok     $1 -> $3"; else echo "  FALLA  $1 -> $3 (se esperaba $2)"; fallas=$((fallas + 1)); fi
}

echo "### $BASE"
comprobar "GET /  (la pagina, por HTTPS)" 200 "$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE/")"
echo "### Estado de ia y del endpoint de SageMaker (GET /api/ia/health)"
"${CURL[@]}" "$BASE/api/ia/health"; echo
comprobar "POST /api/ia/predict SIN clave" 401 \
  "$("${CURL[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE/api/ia/predict" -H 'Content-Type: application/json' -d "$ENTRADA")"
comprobar "POST /api/ia/predict con entrada invalida" 422 \
  "$("${CURL[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE/api/ia/predict" -H 'Content-Type: application/json' -H "X-API-Key: $API_KEY" -d '{"monto_usd":-5}')"
echo "### Prediccion con clave (si el endpoint esta frio, la primera tarda ~6 s y puede salir 503: repetir)"
RESP=$("${CURL[@]}" -w '\n%{http_code}' -X POST "$BASE/api/ia/predict" -H 'Content-Type: application/json' -H "X-API-Key: $API_KEY" -d "$ENTRADA")
echo "${RESP%$'\n'*}" | cut -c1-400
comprobar "POST /api/ia/predict con clave" 200 "${RESP##*$'\n'}"

echo
[ "$fallas" -eq 0 ] && echo "TODO OK" || echo "$fallas comprobacion(es) fallaron"
exit "$fallas"
