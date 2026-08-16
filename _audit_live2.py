#!/usr/bin/env python3
"""Hit the live /turn server with the eval answer questions, parse SSE, print outcome."""
import json, subprocess

QS = [
    "Sa është interesi për 1 depozitë pa afat raiffeisen.",
    "Për shumën maksimale, çfarë norme ka depozita tremujore te banka tirana?",
    "Sa jep credins për depozite 12 mujore në shumën minimale.",
    "Po 1 depozitë 36 mujore në shumën maksimale TOTP sa e ka normën.",
    "Për depozita 12 mujore në shumën minimale sa janë të p dhe union.",
    "Sa është përqindja e komisionit të disbursimit për kredi shtëpie Banka e Bashkuar e Shqipërisë.",
    "Çfarë përqindje ka atë për shlyerjen e parakohshme pjesërisht ose totalisht të kredisë së shtëpisë.",
    "Sa është komisioni në përqindje për ndryshimin e kontratës së kredisë së shtëpisë të bkt.",
    "Sa është komisioni i administrimit në përqindje për kredi konsumatore të pasiguruar të intesa sanpaolo.",
    "Tepër kredit sa është përqindja për shlyerje të parakohshme të kredisë konsumatore të pasiguruar?",
    "Sa është komisioni vjetor i mirëmbajtjes së kartës së kreditit për biznes të raiffeisen.",
    "Sa kushton dhënia e 1 i tëri për kartë krediti biznesi të bkt.",
    "Çfarë vlere ka lëshimi i kartës së debitit për biznes teutë p.",
    "Për 1 kartë debiti biznesi të raiffeisen sa është mirëmbajtja vjetore.",
    "Sa është komisioni minimal për tërheqjen me kartë debiti biznesi në atm të bankave të tjera të Union Bank.",
    "Cilat janë disa nga detyrat kryesore të bankës së shqipërisë?",
    "Çfarë përcakton rregullorja për regjistrin e kredive të bankës së shqipërisë?",
    "Si e vendos 1 bankë e nivelit të 2-të depozitën njëditore në Banka e Shqipërisë dhe kur i kthehen fondet.",
    "Çfarë duhet të mbulojnë treguesit e likuiditetit në planin e rimëkëmbjes së 1 banke?",
    "Çfarë ndodh me 1 subjekt financiar jobankë pasi i revokohet licenca?",
    "Kur mund të klasifikohet sipas kritereve normale 1 kredie ristrukturuar.",
    "Kush është përgjegjës për zbatimin e rregullave të brendshme për rrezikun operacional në 1 bankë?",
    "Çfarë duhet të thotë kontrata e depozitës për maturimin, rinovimin dhe mbylljen para afatit?",
    "Kur e jep ose Banka e Shqipërisë licencën për 1 institucion pagesash.",
    "Më telefononi t 0 7 6 2.123.456 7 se duhet të flas për kartën.",
]
def parse_done(body):
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[6:])
        except Exception:
            continue
        if ev.get("type") == "done":
            return ev
    return {}

import shlex
results = []
for i, q in enumerate(QS):
    payload = json.dumps({"question": q})
    cmd = ["curl", "-s", "--max-time", "120", "-X", "POST",
           "http://127.0.0.1:8000/turn",
           "-H", "Content-Type: application/json",
           "--data-binary", payload]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=130)
        body = out.stdout.decode("utf-8", "replace")
        ev = parse_done(body)
        oc = ev.get("outcome", "??")
        hf = ev.get("handoff", "??")
        pi = ev.get("pii_redacted", "??")
        src = len(ev.get("sources") or [])
        err = out.stderr.decode()[:80]
        results.append((i, oc, hf, pi, src))
        print(f"[{i:2}] {oc:6} handoff={hf} pii={pi} srcs={src:2}  Q={q[:50]}")
        if err and "refused" in err:
            print(f"      curl err: {err.strip()}")
    except Exception as e:
        print(f"[{i:2}] ERROR {e}")
    import time; time.sleep(0.2)
print("---summary---")
from collections import Counter
c = Counter(r[1] for r in results)
print(dict(c))