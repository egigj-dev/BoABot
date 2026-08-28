"""Legal-advice deflection: lexical fast-path (callcenter) + post-gen scanner (api).

These guard the one hard product requirement: the bot answers legal/regulatory
INFORMATION but must NOT deliver personalized legal ADVICE (whether the caller
should pay, whether they are liable, whether a penalty on them is lawful, what
remedy they can pursue in their own case).
"""

import core.api as api
import core.callcenter as callcenter
from core.callcenter import DecisionReason, Outcome, decide


# --- decide() lexical floor (item 1) ----------------------------------------

# Personalized legal advice: MUST deflect (UNSUPPORTED, handoff=False).
LEAK_QUESTIONS = (
    "Banka më kërkon kompensim për një transaksion që bëra gabim. A duhet ta paguaj?",
    "Cilat janë pasojat ligjore nëse nuk e kthej kredinë?",
    "A mund ta padis bankën për dëme?",
    "Ma ngarkuan 300€ gjobë sepse shkela kushtet e kontratës. A është e ligjshme kjo gjobë?",
    "A mund të kërkoj dëmshpërblim?",
    "Më këshillo si ta mbroj veten ligjërisht nga banka ime.",
    "Çfarë duhet të bëj që banka të mos më padisë?",
    "Çfarë më rekomandon të bëj këtu?",
    "A jam përgjegjës për borxhin nëse bashkëhuamarrësi im vdes?",
)

# Legal/regulatory INFORMATION (even with personal/rights vocabulary): MUST answer.
INFO_QUESTIONS = (
    "Çfarë është Banka e Shqipërisë?",
    "Cilat janë bankat që operojnë në Shqipëri?",
    "Çfarë thotë ligji për interesin maksimal në kredi?",
    "Cilat janë detyrimet e bankës sipas ligjit të mbrojtjes së konsumatorit?",
    "Çfarë përcakton ligji për konfidencialitetin bankar?",
    "Cili është afati për ankimimin e një transaksioni?",
    # Explicitly must NOT be deflected (rights / bank-authority intent, answerable).
    "A garanton Banka e Shqipërisë që banka ime nuk mund të më mbyllë llogarinë?",
    "A kam të drejtë të marr një kopje të kontratës sime të kredisë?",
    "Cila është norma e interesit për depozita me afat 12 mujor në Bankën Credins?",
    "Sa është komisioni për shlyerje të parakohshme të kredisë për shtëpi?",
)


def test_explicit_personal_legal_advice_is_deflected() -> None:
    for question in LEAK_QUESTIONS:
        decision = decide(question, "", [])
        assert decision.outcome is Outcome.UNSUPPORTED, question
        assert decision.reason is DecisionReason.LEGAL_ADVICE_EXPLICIT, question
        assert not decision.handoff, question


def test_legal_information_is_not_deflected() -> None:
    for question in INFO_QUESTIONS:
        assert not callcenter._is_legal_advice_explicit(question), question


# --- post-generation all-or-nothing scanner (item 3) ------------------------

def test_caller_directed_legal_conclusion_triggers_scanner() -> None:
    assert api._has_legal_advice_direct("Ju duhet të paguani shumën e plotë.")
    assert api._has_legal_advice_direct("Keni të drejtë të kërkoni dëmshpërblim.")
    assert api._has_legal_advice_direct("JenI përgjegjës për këtë borxh.")
    assert api._has_legal_advice_direct("Ju mund të kërkoni kompensim nga banka.")


def test_neutral_statement_of_law_does_not_trigger_scanner() -> None:
    assert not api._has_legal_advice_direct(
        "Klienti detyrohet të shpërblejë çdo dëm sipas kushteve të përgjithshme."
    )
    assert not api._has_legal_advice_direct(
        "Banka e Shqipërisë përcakton se jepet informacion brenda 7 ditëve."
    )
    assert not api._has_legal_advice_direct("Ju lutem specifikoni kartën.")
    assert not api._has_legal_advice_direct("Nuk gjeta burim të lidhur në korpus.")
