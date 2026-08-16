"""Audit helper: run the full eval answer-question set through decide()."""
import callcenter as cc

QS = [
    ('qa-001', 'Sa është interesi për 1 depozitë pa afat raiffeisen.'),
    ('qa-002', 'Për shumën maksimale, çfarë norme ka depozita tremujore te banka tirana?'),
    ('qa-003', 'Sa jep credins për depozite 12 mujore në shumën minimale.'),
    ('qa-004', 'Po 1 depozitë 36 mujore në shumën maksimale TOTP sa e ka normën.'),
    ('qa-005', 'Për depozita 12 mujore në shumën minimale sa janë të p dhe union.'),
    ('qa-006', 'Sa është përqindja e komisionit të disbursimit për kredi shtëpie Banka e Bashkuar e Shqipërisë.'),
    ('qa-007', 'Çfarë përqindje ka atë për shlyerjen e parakohshme pjesërisht ose totalisht të kredisë së shtëpisë.'),
    ('qa-008', 'Sa është komisioni në përqindje për ndryshimin e kontratës së kredisë së shtëpisë të bkt.'),
    ('qa-009', 'Sa është komisioni i administrimit në përqindje për kredi konsumatore të pasiguruar të intesa sanpaolo.'),
    ('qa-010', 'Tepër kredit sa është përqindja për shlyerje të parakohshme të kredisë konsumatore të pasiguruar?'),
    ('qa-011', 'Sa është komisioni vjetor i mirëmbajtjes së kartës së kreditit për biznes të raiffeisen.'),
    ('qa-012', 'Sa kushton dhënia e 1 i tëri për kartë krediti biznesi të bkt.'),
    ('qa-013', 'Çfarë vlere ka lëshimi i kartës së debitit për biznes teutë p.'),
    ('qa-014', 'Për 1 kartë debiti biznesi të raiffeisen sa është mirëmbajtja vjetore.'),
    ('qa-015', 'Sa është komisioni minimal për tërheqjen me kartë debiti biznesi në atm të bankave të tjera të Union Bank.'),
    ('qa-016', 'Cilat janë disa nga detyrat kryesore të bankës së shqipërisë?'),
    ('qa-017', 'Çfarë përcakton rregullorja për regjistrin e kredive të bankës së shqipërisë?'),
    ('qa-018', 'Si e vendos 1 bankë e nivelit të 2-të depozitën njëditore në Banka e Shqipërisë dhe kur i kthehen fondet.'),
    ('qa-019', 'Çfarë duhet të mbulojnë treguesit e likuiditetit në planin e rimëkëmbjes së 1 banke?'),
    ('qa-020', 'Çfarë ndodh me 1 subjekt financiar jobankë pasi i revokohet licenca?'),
    ('qa-021', 'Kur mund të klasifikohet sipas kritereve normale 1 kredie ristrukturuar.'),
    ('qa-022', 'Kush është përgjegjës për zbatimin e rregullave të brendshme për rrezikun operacional në 1 bankë?'),
    ('qa-023', 'Çfarë duhet të thotë kontrata e depozitës për maturimin, rinovimin dhe mbylljen para afatit?'),
    ('qa-024', 'Kur e jep ose Banka e Shqipërisë licencën për 1 institucion pagesash.'),
    ('qa-029', 'Sa janë normat e depozitave për bizneset.'),
    ('qa-033', 'Më telefononi t 0 7 6 2.123.456 7 se duhet të flas për kartën.'),
    ('qa-035', 'Krijim 4.821 nuk funksionon ma rregulloni.'),
    ('qa-037', 'Ia tregova dikujt open 654.321 dhe tani kam frikë se hyri në llogari.'),
    ('qa-038', 'Nuk e njoh këtë transaksion në llogarinë time. Dua ta kundërshtoj tani.'),
]

bad = 0
mismatch = 0
for k, q in QS:
    d = cc.decide(q, '', [])
    oc = d.outcome.value if d.outcome else 'ANSWER'
    print(f'{k:8} {oc:12} handoff={int(d.handoff)} pii={int(d.pii_redacted)} '
          f'score={d.handoff_score if d.handoff_score is not None else -99:.4f}')
print('done')