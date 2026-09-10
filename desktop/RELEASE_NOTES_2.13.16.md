# OpsNest 2.13.16 — kandidat za objavu

Datum: 10.09.2026. Osnova: `2bc0a2d` (2.13.15).

## Obim

Popravke postojećeg OpsNesta, bez redizajna, novih navigacionih tokova, zamene šablona ili migracije poslovne baze. Desktop, web i javni sajt ostaju postojeći proizvodi; nisu prebačeni na drugi hosting. Spoljne integracije nisu uključene.

## Ispravke

- Registracija i podaci firme koriste iste izbore države/jezika/valute. Srbija i Bugarska su lako dostupne, EUR je jasno imenovan, BGN označen kao istorijski. Pet jezika forme; stabilni kodovi i nesnimljeni unos preživljavaju promenu jezika.
- PDV 0 i rok plaćanja 0 više se ne zamenjuju podrazumevanim vrednostima. Država/KD predlažu operativna podešavanja, ne nagađaju pravnu formu ni PDV status. Jezik interfejsa ostaje nezavisan od države.
- RSD/EUR/BGN tok odobrenja, povraćaja i korekcije čuva valutu originala. Odobrenje se ograničava originalnim iznosom, ne ponavlja već iskorišćen povraćaj i poštuje pristup i zaključan period.
- Radni PDV izveštaj i izvoz za knjigovođu koriste jednu eksplicitnu valutu; izuzetke drže odvojeno. Nema prećutnog sabiranja ili konverzije različitih valuta.
- Tri standardna PDF izlaza proverena su u A4 portretu, sa prelamanjem naziva i pravilnim valutama. Bugarske sistemske oznake povraćaja/odobrenja su prevedene. Korisnički slobodan tekst se ne menja.
- Postojeći web radni prostor dobija BG uz SR/EN. Dinamički podaci se prevode bez promene poslovnih kodova, naziva firmi, korisničkog teksta ili nesnimljenih komentara.
- Sajt koristi zajedničku API rutu za preuzimanje; nepotpuni aktivacioni/naplatni linkovi daju razumljivu grešku umesto tehničkog 422. Validacija pristupa ostaje uključena.
- Build prekida rad na grešci i pakuje samo javne resurse iz eksplicitne liste.

## Provere

- Python: 66/66 uspešno na Windows 11 / Python 3.13.14 (53,2 s).
- Portal: 4/4 uspešno, Node test runner + jsdom 26.1.0.
- Forma: 26 država × 5 jezika, uključujući očuvanje unosa i vraćanje oznaka na poslovne kodove.
- PDF: pregledana prva/jedina strana sva tri QA izlaza; proveren A4 format svake generisane strane.
- Sintaksa izvora i `git diff --check`: prolaze.
- Linux/Windows CI je proširen, ali udaljeni CI nije pokrenut ovom lokalnom proverom.

## Artefakt

Status: **lokalni instalater je napravljen i sadržaj proveren; nije objavljen, pokrenut niti instaliran**.

- Putanja: `desktop/release/OpsNest-Setup-2.13.16.exe`
- Veličina: `114218582` bajtova.
- SHA-256 instalatera: `1fb1928e0e47c7078f645571124d8395689fd66cffc9ab05d5782444d7a51b54`
- SHA-256 upakovane aplikacije: `dfee3a857266e3a70c8c308e26b35b33f8eacb9f48ebeae1e7d5b678bba3fac4`
- Pročitana verzija iz programskog i instalacionog paketa: oba `2.13.16`.
- Bajtovi aplikacije ugrađeni u instalater identični su provereno izgrađenoj aplikaciji.
- Novi prevodi i potrebni moduli prisutni su u upakovanom kodu. Proverena je dozvoljena lista šest javnih resursa; nema baze, `.env` ili `.ndjson` fajlova u instalateru.
- Windows Authenticode status: **NotSigned**. Nije podešen certifikat za potpisivanje izdavača. To nije prećutano niti je SHA-256 predstavljen kao digitalni potpis.

Napravljen PyInstaller-om 6.22.2 / Python 3.13.14. Build prijavljuje opciona upozorenja o zavisnostima; funkcionalna proba pokrenutog instaliranog paketa tek predstoji. Ne koristiti ovaj dokument kao potvrdu javne objave ili uspešne produkcione instalacije.

## Redosled objave i provere

1. Sačuvati i proveriti rezervnu kopiju produkcionih podataka i korisničkog šablona. Instalacioni smoke test prvo izvršiti nad izolovanim profilom, ne nad poslovnom bazom.
2. Obezbediti potpis izdavača ili posebno odobrenje za nepotpisan testni paket. SHA-256 potvrđuje identičnost preuzimanja, nije digitalni potpis izdavača.
3. Objaviti API izmene sa postojećim manifestom 2.13.15. Proveriti `/download/desktop` (307, pouzdana HTTPS lokacija), `/workspace`, `/health/ready` i razumljive 400 odgovore nepotpunih linkova.
4. Postaviti `OpsNest-Setup-2.13.16.exe` u postojeći `public_html/downloads`. Ne postavljati izvorni kod, bazu, `.env`, privatne šablone ili QA podatke.
5. Preuzeti javni instalater i proveriti veličinu i SHA-256 naspram lokalnog artefakta. Tek nakon poklapanja zajedno promeniti `DESKTOP_LATEST_VERSION`, `DESKTOP_INSTALLER_URL`, `DESKTOP_INSTALLER_SHA256` i ažurirati rezervni manifest u izvoru.
6. Objaviti pripremljeni sadržaj `public_site`, sa postojećim `.htaccess`. Ne brisati folder `downloads`.
7. Potvrditi da sajt, javni update API i desktop preuzimaju isti verifikovan paket. Potvrditi instalaciju/povratak na prethodnu verziju uz očuvanje poslovnih podataka.

Ni jedan od ovih produkcionih koraka nije izvršen samom izradom lokalnog kandidata. Trenutna javna verzija ostaje 2.13.15.

## Granice provere

Nisu potvrđeni svi legacy desktop prevodi, svaki korisnikov Excel/COM šablon, svi produkcioni klikovi/uloge/DPI, paralelan rad velikog tima, automatske kursne konverzije ili zakonska usklađenost svih delatnosti/država. Dokument arhiva i državne/bankarske integracije ostaju prema prethodnom izboru neaktivirane. Slobodni korisnički tekst ne prevodi se automatski putem spoljnog servisa.
