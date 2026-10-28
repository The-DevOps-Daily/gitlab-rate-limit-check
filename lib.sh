# Shared by the probe scripts. fetch sets $status to the final HTTP status
# (from curl's write-out, so a proxy's "Connection established" never counts)
# or ERR<curl exit code>, and $headers to the last response's header block.
# -q ignores any ~/.curlrc so the probes send exactly what they say.
fetch() {
  local hdr
  hdr=$(mktemp)
  status=$(curl -q -s -o /dev/null -D "$hdr" -w '%{http_code}' "$@") || status="ERR$?"
  headers=$(tr -d '\r' < "$hdr" | awk '/^HTTP\// { block = "" } { block = block $0 "\n" } END { printf "%s", block }')
  rm -f "$hdr"
}

header() { awk -v k="$1" 'tolower($1) == tolower(k ":") { print $2 }' <<<"$headers"; }
