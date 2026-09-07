#!/usr/bin/env sh
# ─────────────────────────────────────────────────────────────────────────────
#  Genera un certificado autofirmado para desarrollo (RNF003).
#
#  ⚠️  Solo para desarrollo y para la simulacion. El despliegue sobre la Pi 5
#      usa un certificado emitido por la CA interna del laboratorio.
#
#  Uso:  sh docker/nginx/gen-certs.sh
# ─────────────────────────────────────────────────────────────────────────────
set -eu

# En Git Bash / MSYS (Windows) la conversion automatica de rutas convierte el
# argumento -subj "/C=AR/..." en una ruta de Windows y openssl lo rechaza. Se
# exime solo a ese argumento: -keyout y -out SI necesitan la conversion. Es
# inocuo en Linux y macOS, donde la variable simplemente se ignora.
MSYS2_ARG_CONV_EXCL='/C='
export MSYS2_ARG_CONV_EXCL

DIR="$(cd "$(dirname "$0")" && pwd)/certs"
mkdir -p "$DIR"

if [ -f "$DIR/co2.crt" ]; then
    echo "Ya existe $DIR/co2.crt — no se sobrescribe."
    echo "Para regenerarlo: rm $DIR/co2.crt $DIR/co2.key && sh $0"
    exit 0
fi

openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -keyout "$DIR/co2.key" \
    -out    "$DIR/co2.crt" \
    -subj   "/C=AR/O=Grupo 10/OU=CO2/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:nginx,DNS:co2.local,IP:127.0.0.1"

chmod 600 "$DIR/co2.key"
echo "Certificado generado en $DIR"
echo "El browser va a advertir que no es de confianza: es esperable en un autofirmado."
