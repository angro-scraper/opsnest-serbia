# OpsNest 2.13.17 — Founder bez paketnih ograničenja

## Obim

Ispravka postojećeg osnivačkog naloga: nema promene dizajna, poslovnih podataka, šablona, lozinke, uloga niti uključivanja spoljnih integracija.

- Server dodeljuje Founder samo potvrđenom vlasničkom e-mailu iz `FOUNDER_WORKSPACE_EMAILS`. To nije kupovni paket niti izbor u registraciji.
- Founder nema paketni limit korisničkih mesta, projekata, izdatih faktura, PDF uvoza ili mesečnih AI saveta. Broj AI zahteva se i dalje evidentira; zaštita od previše zahteva, prava pristupa i tehnička ograničenja provajdera ostaju.
- API radi kompatibilnosti zadržava `plan_code=pro`, ali vraća `access_source=founder`, naziv Founder, eksplicitne limite `null` i `ai_advisor.unlimited=true`.
- Desktop pamti potvrđeni status, proverava radni prostor licence, čuva ga pri sopstvenoj sinhronizaciji i uklanja pri odjavi/povezivanju drugog radnog prostora ili redovnoj licenci sa servera. Sinhronizovani poslovni paket ne prenosi Founder pravo drugom uređaju.
- Osvežavanje licence i AI sada rade i preko važeće centralne vlasničke sesije bez starog tokena pretplate. AI putanja dozvoljava samo vlasnika; ostale uloge nisu unapređene.
- Web, administratorski prikaz i desktop prikazuju Founder/Neograničeno umesto Pro/20. Kupovina dodatnog paketa za Founder se blokira.
- Ostali korisnici zadržavaju svoje postojeće pakete i limite.

## Provere pre objave

- Windows / Python 3.13: 77 testova uspešno (60,7 s), uključujući 9 novih Founder regresija.
- Portal / jsdom: 5 testova uspešno, uključujući SR/BG/EN Founder i običan Pro prikaz.
- Provereni: opoziv prava, nepotvrđen e-mail, više od 20 mesta, AI posle 300 zahteva, zadržana ograničenja uloga/rate-limit kontrola, ponovno pokretanje, sinhronizacija i odbijanje licence tuđeg radnog prostora.
- Testovi ne šalju stvarne pozivnice niti zahteve plaćenom AI servisu.

## Objava

Instalater je objavljen i ponovo preuzet sa identičnim SHA-256. Javni manifest se tek posle te provere prebacuje na 2.13.17. Autentičan Windows potpis izdavača nije konfigurisan (NotSigned); SHA-256 je provera integriteta, ne digitalni potpis.

- Instalater: `OpsNest-Setup-2.13.17.exe`, 114222143 bajta.
- SHA-256: `259ac4b790a7dbb276d761a8cf47199a1c694962267a49268526120196c8ebd9`.
- SHA-256 upakovane aplikacije: `d70680ec46a111c5c92e185aa78ff3214c4a3a4036da4d980935e0eb60c18f3c`.
- Verzija 2.13.17 potvrđena u obe upakovane komponente, ugrađena aplikacija identična izgrađenoj, Founder politika proverena direktno iz programskog paketa, nema poslovne baze ni privatnih šablona.
- Server sa izvornim commitom `0a25ff09a3220afa79d558be9a28dece30cb4f00` je Live; `/health/ready` vraća ready/database ok. Arhiva dokumenata ostaje neaktivirana prema prethodnom izboru.
- Sačuvana lokalna vlasnička sesija pri read-only proveri vraća 401 (istekla/nevažeća). Nije menjana lozinka niti produžavana sesija mimo pravila. Potrebna je jedna ponovna prijava; konkretan nalog nije ponovo autentifikovan u ovoj proveri.

Posle instalacije: Paketi i plaćanje → Osveži status. Koristi se postojeći nalog i postojeća sačuvana prijava.
