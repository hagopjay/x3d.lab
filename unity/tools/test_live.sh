#!/usr/bin/env bash
# End-to-end test of the live server + gate (needs node and an x3d_mcp checkout; see gate.py).
set -u; cd "$(dirname "$0")"; P=${PORT:-8791}
node live_server.mjs --port $P >/tmp/live.log 2>&1 & S=$!; sleep 1; fail=0
chk() { if [ "$2" = "$3" ]; then echo "  ok   $1 ($2)"; else echo "  FAIL $1 (got $2, want $3)"; fail=1; fi; }
chk "empty server 404" "$(curl -s -o /dev/null -w '%{http_code}' localhost:$P/manifest.json)" 404
chk "valid scene published" "$(curl -s -o /tmp/push.json -w '%{http_code}' --data-binary @../samples/scenes/hello-xr.x3d localhost:$P/push)" 200
E=$(curl -si localhost:$P/manifest.json | tr -d '\r' | awk -F': ' 'tolower($1)=="etag"{print $2}')
chk "manifest 200" "$(curl -s -o /dev/null -w '%{http_code}' localhost:$P/manifest.json)" 200
chk "manifest 304 on matching etag" "$(curl -s -o /dev/null -w '%{http_code}' -H "If-None-Match: $E" localhost:$P/manifest.json)" 304
chk "scene round-trips" "$(curl -s localhost:$P/scene.x3d | sha256sum | cut -c1-64)" "$(sha256sum ../samples/scenes/hello-xr.x3d | cut -c1-64)"
BAD='<X3D profile="Interchange" version="4.0"><Scene><Transform DEF="T"><Shape><Box/></Shape></Transform><ROUTE fromNode="Nope" fromField="a" toNode="T" toField="set_translation"/></Scene></X3D>'
chk "XSD-valid but semantically broken scene (dangling ROUTE) refused (422)" "$(curl -s -o /tmp/bad.json -w '%{http_code}' --data "$BAD" localhost:$P/push)" 422
chk "refused scene did NOT replace the live one" "$(curl -s localhost:$P/scene.x3d | sha256sum | cut -c1-64)" "$(sha256sum ../samples/scenes/hello-xr.x3d | cut -c1-64)"
BADX='<X3D profile="Interchange" version="4.0"><Scene><Shape><Box size="1 1"/></Shape></Scene></X3D>'
chk "XSD-invalid scene (Box size with 2 numbers) refused (422)" "$(curl -s -o /dev/null -w '%{http_code}' --data "$BADX" localhost:$P/push)" 422
echo "  gate said: $(python3 -c "import json;d=json.load(open('/tmp/bad.json'))['gate'];print('xsd errors',len(d['xsd']['errors']),'| semantic errors',len(d['semantic']['errors']))")"
kill $S 2>/dev/null; exit $fail
