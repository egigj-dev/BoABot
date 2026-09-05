from core.comparison import parse_rate_intent, structured_rate_hits

def test_business_values_keep_bank_attribution():
    parsed = parse_rate_intent('normat e biznesit te vogel me maturitet 0-12 muaj')
    assert parsed.status == 'resolved'
    text = structured_rate_hits(parsed.intent)[0]['text']
    assert 'Banka e Bashkuar e Shqipërisë: 9.00' in text
    assert 'Banka Procredit: 10.50' in text
