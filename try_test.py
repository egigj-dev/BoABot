# from voice.fidelity_guard import _number, VALUE_RE
# for s in ["0.50", "0,50", "1.00", "2.00", "10.000", "4.75"]:
#     try: print(f"{s!r} -> {_number(s)}")
#     except Exception as e: print(f"{s!r} -> ERROR {e}")
# # and confirm both sides tokenize the same way
# print([m.groupdict() for m in VALUE_RE.finditer("Minimumi (MIN): 0.50")])

from voice.fidelity_guard import FidelityGuard
import retrieve
g = FidelityGuard()
h = retrieve.retrieve("komisioni shlyerje e parakohshme Banka Credins", k=3)
for c in h:
    print(repr(c["text"][:200]))
    print("  claims:", g.extract_claims(c["text"]))
print("---- answer-style line ----")
print(g.extract_claims("*   Minimumi (MIN): 0.50\n*   Përqindja (%): 1.00"))