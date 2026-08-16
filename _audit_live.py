import json, urllib.request

QS = [
    "Sa është interesi për 1 depozitë pa afat raiffeisen.",
    "Për shumën maksimale, çfarë norme ka depozita tremujore te banka tirana?",
    "Sa jep credins për depozite 12 mujore në shumën minimale.",
    "Sa është përqindja e komisionit të disbursimit për kredi shtëpie Banka e Bashkuar e Shqipërisë.",
    "Cilat janë disa nga detyrat kryesore të bankës së shqipërisë?",
    "Çfarë duhet të mbulojnë treguesit e likuiditetit në planin e rimëkëmbjes së 1 banke?",
    "Më telefononi t 0 7 6 2.123.456 7 se duhet të flas për kartën.",
]
def parse(body):
    done = None
    for line in body.decode().splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[6:])
        except Exception:
            continue
        if ev.get("type") == "done":
            done = ev
    return done

for i, q in enumerate(QS):
    req = urllib.request.Request(
        "http://127.0.0.1:8000/turn",
        data=json.dumps({"question": q}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
        ev = parse(body)
        oc = ev.get("outcome") if ev else "??"
        hf = ev.get("handoff") if ev else "??"
        purl = ev.get("pii_redacted") if ev else "??"
        print(f"[{i}] outcome={oc} handoff={hf} pii={purl}  Q={q}")
    except Exception as e:
        print(f"[{i}] ERROR {e}  Q={q}")
print("live-done")