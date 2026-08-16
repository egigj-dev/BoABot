#!/bin/bash
# Audit: hit the live /turn server (127.0.0.1:8000) with the eval answer questions and report outcome.
QS=(
  "Sa është interesi për 1 depozitë pa afat raiffeisen."
  "Për shumën maksimale, çfarë norme ka depozita tremujore te banka tirana?"
  "Sa jep credins për depozite 12 mujore në shumën minimale."
  "Sa është përqindja e komisionit të disbursimit për kredi shtëpie Banka e Bashkuar e Shqipërisë."
  "Cilat janë disa nga detyrat kryesore të bankës së shqipërisë?"
  "Çfarë duhet të mbulojnë treguesit e likuiditetit në planin e rimëkëmbjes së 1 banke?"
  "Më telefononi t 0 7 6 2.123.456 7 se duhet të flas për kartën."
)
for i in "${!QS[@]}"; do
  q="${QS[$i]}"
  out=$(curl -s -X POST http://127.0.0.1:8000/turn -H "Content-Type: application/json" --data-binary "$(printf '{"question": "%s"}' "$q")")
  oc=$(printf '%s' "$out" | grep -oE '"outcome": ?"[a-z]+"' | head -1)
  hf=$(printf '%s' "$out" | grep -oE '"handoff": ?(true|false)' | head -1)
  pi=$(printf '%s' "$out" | grep -oE '"pii_redacted": ?(true|false)' | head -1)
  echo "[$i] ${oc} ${hf} ${pi}  Q: $q"
done
echo "live-done"