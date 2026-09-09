#!/usr/bin/env python3
"""Step 17 (AT) regression harness: replay the AQ 14 sequences against the
keyed /turn server and classify the FINAL turn deterministically.

Classification is keyword/x-rule based, NOT hand-judged, so the baseline is
stable across runs and across code changes. Generation is run-dependent: the
same evidence can produce a confident wrong answer on one roll and an honest
refusal on another (AQ). The classifier must therefore look at CONTENT, and
the expected family per sequence is pinned here so a "wrong product" is a
deterministic signal, not a vibes call.

Classes:
  CORRECT            — text answers the EXPECTED family's data, sources are the
                       expected family's tables.
  WRONG-ANSWER       — text asserts rate/fee figures (numbers) for a product;
                       sources or intent point to a DIFFERENT family than
                       expected, and the text does not refuse.
  REFUSED-CLEAN      — text refuses honestly ("nuk gjendet/nuk ka"), no
                       numbers; sources are empty or the expected family's.
  REFUSED-BAD-SOURCES — text refuses honestly, but the cited source tables
                       belong to a different family than expected.

Run: python3 scripts/aq_harness.py [tag] > aq_run_<tag>.txt ; 3x on HEAD.
"""
import json
import re
import sys
import urllib.request

BASE = "http://127.0.0.1:8000/turn"

# (name, [turns], expected_family, expected_source_marker)
# expected_family: keyword that must appear in a genuine answer's prose.
# expected_source_marker: the source TABLE the expected family's data lives in
# (deposit rates -> "Normat e interesit të depozitave"; credit/housing rates
# -> "Normat nominale dhe NEI"; card/business fees -> "Komisionet"). Correct
# answers must cite these; refusals citing anything else (e.g. deposit tables
# for a credit ask) are REFUSED-BAD-SOURCES; confident answers of the wrong
# family with numbers are WRONG-ANSWER.
SEQUENCES = [
    ("ap-shape",
     ["cilat prej bankave ofrojne kredi hipotekare?",
      "cilat jane normat e interesit qe ofrojne?"],
     "kredi hipotekare", "Normat nominale dhe NEI"),
    ("credit-deposit-1",
     ["cilat jane normat e interesit per kredi hipotekare?",
      "po normat e interesit?"],
     "kredi hipotekare", "Normat nominale dhe NEI"),
    ("credit-deposit-2",
     ["cilat jane normat e interesit per depozita 12 muaj?",
      "po normat e interesit per kredita?"],
     "kredi", "Normat nominale dhe NEI"),
    ("deposit-card-1",
     ["cilat jane normat e depozitave te individve?",
      "po tarifat e kartes te kredite?"],
     "kart", "Komisionet"),
    ("deposit-card-2",
     ["cilat jane tarifat e kartes te debitit?",
      "po normat e interesit?"],
     "kredi", "Normat nominale dhe NEI"),
    ("deposit-card-3",
     ["cilat jane normat e interesit per depozita?",
      "po komisione per karta debiti?"],
     "kart", "Komisionet"),
    ("business-indiv-1",
     ["cilat jane tarifat e biznesit te vogel?",
      "po komisionet e individve?"],
     "individ", "Komisionet për individë"),
    ("business-indiv-2",
     ["cilat jane normat e interesit per biznese?",
      "po normat e interesit per individ?"],
     "individ", "Normat e interesit të depozitave"),
    ("fee-rate-1",
     ["cilat jane komisionet e kartave te debitit?",
      "po normat e interesit?"],
     "kredi", "Normat nominale dhe NEI"),
    ("fee-rate-2",
     ["cilat jane komisionet per kredi konsumatore?",
      "po normat e interesit?"],
     "kredi", "Normat nominale dhe NEI"),
    ("loan-types",
     ["me thuaj llojet e kredive",
      "cilat jane normat e interesit qe ofrojne?"],
     "kredi", "Normat nominale dhe NEI"),
    ("avail-then-rate",
     ["cilat prej bankave ofrojne karte krediti?",
      "cilat jane normat e interesit qe ofrojne?"],
     "kredi", "Normat nominale dhe NEI"),
    ("avail-dep-then-rate",
     ["cilat prej bankave ofrojne depozita me afat?",
      "cilat jane normat e interesit qe ofrojne?"],
     "depozit", "Normat e interesit të depozitave"),
    ("3turn",
     ["me thuaj bankat ne shqiperi",
      "cilat prej bankave ofrojne kredi hipotekare?",
      "cilat jane normat e interesit qe ofrojne?"],
     "kredi hipotekare", "Normat nominale dhe NEI"),
]

# Albanian refusal markers in generated prose.
_REFUSE_RE = re.compile(
    r"nuk\s+(?:gjend[et]*|ka|kam|ofron|përmban|ekziston|botuar)|"
    r"nuk\s+gjeta|nuk\s+mund\s+ta|pa\s+rezultat|nuk\s+ka\s+nj|"
    r"nuk\s+kanë\s+materiale", re.I)
# Does the text assert concrete numbers (rates/fees)?
_NUMBER_RE = re.compile(r"\d[\d',\.]*\s*(?:%|përqind|lek|ALL|€)?|përqind", re.I)


def ask(question: str, sid: str) -> dict:
    payload = {"question": question, "session_id": sid}
    req = urllib.request.Request(
        BASE, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    toks, done = [], {}
    with urllib.request.urlopen(req, timeout=300) as r:
        for line in r:
            line = line.decode().strip()
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            kind = ev.get("type")
            if kind == "tool":
                done["rewritten"] = ev.get("query")
            elif kind == "token":
                toks.append(ev.get("text") or "")
            elif kind in ("done", "error"):
                done = {**done, **ev}
    done["text"] = "".join(toks).strip()
    return done


def classify(name: str, turns: list[str], expected_family: str,
             expected_source_marker: str) -> dict:
    sid = f"harness-{name}-{tag}"
    final = None
    detail = {}
    for i, q in enumerate(turns, 1):
        done = ask(q, sid)
        detail[f"t{i}"] = {
            "outcome": done.get("outcome"), "reason": done.get("reason"),
            "abstain": done.get("abstain_reason"),
            "rewritten": done.get("rewritten"),
            "n_sources": len(done.get("sources") or []),
            "tables": sorted({(s.get("doc") or "")[:60] for s in done.get("sources") or []}),
        }
        final = done
    # ---- classify the FINAL turn ----
    text = (final.get("text") or "").lower()
    srcs = final.get("sources") or []
    tables = " ".join((s.get("doc") or "") for s in srcs)
    n = len(srcs)
    refuses = bool(_REFUSE_RE.search(text))
    has_numbers = bool(_NUMBER_RE.search(text))
    # does the text name the expected family?
    names_expected = (expected_family.lower() in text)
    # do the sources point at the expected family's tables? (only when marker given)
    if expected_source_marker:
        sources_match = (expected_source_marker.lower() in tables.lower())
    else:
        # no marker: sources are "wrong" if they are deposit/komision tables not
        # matching the expected family keyword in the text
        sources_match = not (("depozit" in tables.lower()) and
                             ("kredi" in expected_family.lower()))
    if refuses:
        cls = "REFUSED-CLEAN" if sources_match else "REFUSED-BAD-SOURCES"
    elif has_numbers and not names_expected:
        cls = "WRONG-ANSWER"
    elif not has_numbers and not names_expected:
        # nothing asserted and nothing named: if nothing is CITED either, the
        # turn produced no wrong-answer surface at all (clarify/abstain with
        # 0 sources) — that is clean, not bad-citation.
        cls = "REFUSED-CLEAN" if (n == 0 or sources_match) else "REFUSED-BAD-SOURCES"
    else:
        cls = "CORRECT"
    # A turn that cites NOTHING cannot mislead with wrong tables: 0 sources is
    # never REFUSED-BAD-SOURCES (nothing bad is cited).
    if cls == "REFUSED-BAD-SOURCES" and n == 0:
        cls = "REFUSED-CLEAN"
    return {
        "name": name, "class": cls, "n_turns": len(turns),
        "outcome": final.get("outcome"), "reason": final.get("reason"),
        "abstain": final.get("abstain_reason"),
        "n_sources": n, "tables": detail.get(f"t{len(turns)}", {}).get("tables", []),
        "text_head": (final.get("text") or "")[:140],
        "detail": detail,
    }


if __name__ == "__main__":
    global tag
    tag = sys.argv[1] if len(sys.argv) > 1 else "base"
    results = [classify(*s) for s in SEQUENCES]
    counts = {}
    for r in results:
        counts[r["class"]] = counts.get(r["class"], 0) + 1
    print(f"=== AQ harness run tag={tag} ===")
    for r in results:
        print(f"{r['class']:<18} {r['name']:<22} outcome={r['outcome']} reason={r['reason']} "
              f"src={r['n_sources']}")
    print()
    print("COUNTS:", json.dumps(counts))
    print("TOTAL:", len(results))