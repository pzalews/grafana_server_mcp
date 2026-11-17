#!/bin/sh
# If an extra CA bundle is mounted (GRAFANA_EXTRA_CA_FILE in compose), append it to a copy
# of the system bundle and point gcx (Go) at it via SSL_CERT_FILE. Runs as non-root.
set -eu

EXTRA_CA=/run/extra-ca.pem
BUNDLE=/tmp/ca-bundle.pem

if [ -s "$EXTRA_CA" ]; then
    cat /etc/ssl/certs/ca-certificates.crt "$EXTRA_CA" > "$BUNDLE"
    export SSL_CERT_FILE="$BUNDLE"
fi

exec grafana-mcp "$@"
