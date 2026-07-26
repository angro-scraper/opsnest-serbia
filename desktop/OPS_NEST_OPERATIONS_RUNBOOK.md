# OpsNest — operativni priručnik firme

Ovaj priručnik omogućava da finansije i projekti nastave bez zastoja kada je
vlasnik, glavni knjigovođa ili bilo koji član tima odsutan. OpsNest je operativni
sistem firme; lokalna poreska prijava i zakonski završni račun ostaju predmet
provere ovlašćenog računovođe u svakoj državi.

## 1. Obavezne uloge

Za svaku aktivnu firmu moraju postojati najmanje dva aktivna centralna naloga:

| Uloga | Minimalan broj | Odgovornost | Ne sme samostalno |
| --- | ---: | --- | --- |
| Vlasnik | 1 | Limiti, izdavanje/odobravanje, plaćanja, periodi, pristup | Odobravati dokument koji je sam pripremio ili biti jedina osoba sa pristupom |
| Administrator — zamenik | 1 | Preuzima rad vlasnika, šalje pozive, ukida pristup, kontrola sinhronizacije | Menjati/brisati istoriju bez opravdanja |
| Glavni knjigovođa | 1 | PDV radne evidencije, kontrole, izvozi, zatvaranje perioda | Odobravati sopstvenu fakturu ili sopstvenu obavezu |
| Menadžer projekta | po potrebi | Ugovor, budžet, projekat, priprema nacrta | Izdavati bez odobrenja vlasnika kada je tok uključen |
| Operater | po potrebi | Dokumenti, troškovi, uplate, priprema podataka | Menjati licence, članove tima ili izdavati dokumente bez dozvole |

Vlasnik i zamenik ne dele istu lozinku. Svako koristi svoj poslovni e-mail i
svoj centralni nalog. Pristup se daje kroz **OpsNest tim**, ne deljenjem baze ili
kopiranjem fajlova preko privatnih kanala.

## 2. Pravilo četiri oka

Nijedna važna finansijska radnja ne sme ostati bez nezavisne kontrole:

| Tok | Priprema | Provera/odobrenje | Dokaz u OpsNest-u |
| --- | --- | --- | --- |
| Izlazna faktura | Knjigovođa ili menadžer projekta | Vlasnik/administrator | Nacrt → proveravanje → odobrenje/izdavanje + istorija fakture |
| Povrat na doradu | Vlasnik/administrator | — | Obavezan komentar u istoriji fakture |
| Avans | Menadžer projekta | Vlasnik + knjigovođa | Ugovor projekta, procenat avansa, avansni račun |
| Završni račun | Knjigovođa/menadžer | Vlasnik/administrator | Plaćeni avans povezan sa završnim računom |
| Ulazna obaveza | Operater/knjigovođa | Vlasnik/administrator | Dobavljač, prilog, komentar, odobrenje/odbijanje |
| Obaveza na/iznad limita | Knjigovođa/administrator priprema | Vlasnik | Limit u osnovnoj valuti firme; strana valuta ide vlasniku dok ne postoji odobren FX model |
| Plaćanje | Knjigovođa priprema | Ovlašćeni nalogodavac u banci | Plan plaćanja, bankovni izvod i finansijski audit |
| Storno/ispravka | Knjigovođa priprema razlog | Vlasnik/administrator | Storno ili korektivni dokument; izdata faktura se ne prepravlja |

## 3. Dnevni ritam

### Operater / projekat

1. Unosi pristigle dokumente, troškove i bankovne prilive/odlive.
2. Vezuje dokument uz projekat, kupca ili dobavljača.
3. Ne briše knjižene ili izdate dokumente; grešku označava komentarom i šalje na proveru.
4. Pre kraja dana proverava da nijedan dokument nije ostao bez projekta ili datuma.

### Knjigovođa

1. Pregleda nacrte, stavke, PDV, kupca, rok plaćanja i jezik dokumenta.
2. Šalje ispravne fakture na odobravanje; kada su vraćene na doradu čita komentar u **Istoriji fakture**, ispravlja i ponovo šalje.
3. Unosi i kontroliše obaveze dobavljača i predlog plaćanja. **Operativni
   centar** posebno izdvaja obavezu bez originalnog dokumenta i odbijenu
   obavezu, tako da nijedna stavka ne ostane izgubljena između prijema,
   dorade i odobrenja.
4. Usklađuje banku: svaki priliv/odliv mora biti povezan ili označen kao izuzetak.
5. Pokreće **Finansije → Kreiraj dospele troškove** za odobrene ponavljajuće
   obaveze. Komanda je bezbedna za ponovno pokretanje: isti period ne sme
   stvoriti drugu obavezu ni posle prekida rada aplikacije.
6. Dnevno proverava dospela potraživanja, otvorene obaveze i finansijski audit.

### Vlasnik / administrator

1. Pregleda red **Odobrenja** i odlučuje: odobri za izdavanje, odobri i izdaj ili vrati na doradu sa komentarom.
2. Pregleda obaveze za plaćanje i potvrđuje samo one sa dokumentom i odobrenjem.
3. Kada je uključen limit vlasnika, samo vlasnik odobrava obaveze na/iznad limita i sve strane valute; administrator ne sme zaobilaziti limit ručnim kursom.
4. Dnevno prati Dashboard i Finansije: dospeća, likvidnost, cash-flow i izuzetke.
5. Proverava da centralna sinhronizacija ima poslednju reviziju pre završetka radnog dana.

## 4. Nedeljne i mesečne kontrole

### Svakog petka

- Pregled svih faktura na čekanju, vraćenih nacrta i neusklađenih bankovnih stavki.
- Pregled obaveza koje dospevaju u narednih 7/30 dana.
- Provera da svaki aktivni projekat ima kupca, ugovor (ako postoji), odgovornu osobu i dokumente.
- Izvoz projekta za knjigovođu za projekte sa većim prometom ili rizikom.

### Na kraju meseca

- Uskladiti banku, kupce, dobavljače i PDV radne evidencije.
- Pregledati firma-wide P&L, cash-flow 7/30/90 i otvorena dugovanja.
- Proveriti da je limit odobrenja vlasnika i lista stranih valuta i dalje primerena ovlašćenjima firme; svaku promenu evidentirati kroz podatke firme i mesečnu kontrolu.
- Knjigovođa priprema lokalne poreske radne izveštaje; ovlašćeni lokalni stručnjak ih proverava pre zvanične prijave.
- Vlasnik odobrava zaključavanje obračunskog perioda tek kada je kontrolna lista potpuna.
- Sačuvati računovodstveni izvoz i u **Finansije → Mesečna kontrola** izabrati
  **Napravi i proveri backup**. OpsNest tada pravi SQLite snapshot i proverava
  njegov integritet; stavka backupa ne sme se zatvarati samo ručnim čekiranjem.
- Pre zaključavanja izvesti **Finansije → Izvezi audit** i čuvati CSV zajedno
  sa njegovim `.sha256.txt` kontrolnim fajlom u mesečnoj arhivi. Sam izvoz se
  beleži u finansijskom auditu sa imenom osobe koja ga je uradila.
- U **Finansije → Mesečna kontrola** izabrati **Proveri audit lanac**. Samo
  pozitivan rezultat se može označiti kao završena kontrola. Ako lanac nije
  ispravan, ne zatvarati period: sačuvati postojeći provereni backup i otvoriti
  incident kod administratora.
- Pri samom zatvaranju OpsNest ponovo proverava audit lanac i SQLite integritet
  poslednjeg backupa; backup mora biti čitljiv i noviji od 24 sata. Ručno
  označena kontrola bez tih stvarnih provera nije dovoljna za zaključavanje.

## 5. Predaja posla i odsustvo

Pre odsustva osoba koja vodi finansije mora uraditi sledeće:

1. U **OpsNest tim** potvrditi da je zamenik aktivan administrator i da može da preuzme podatke.
2. Poslati poslednje izmene u zajednički prostor i proveriti reviziju sinhronizacije.
3. Ostaviti listu otvorenih stavki: fakture na odobrenju, vraćene na doradu, dospele obaveze, bankovni izuzeci i rokovi za državu.
4. Za svaku stavku upisati komentar u odgovarajući dokument ili finansijski tok — nikada samo u privatnoj poruci.
5. Zamenik preuzima podatke na svom računaru i proverava Dashboard, Odobrenja, Finansije i jedan projekat od početka do kraja.

Po povratku osoba najpre preuzima poslednju zajedničku verziju podataka, pa tek onda unosi nove izmene.

## 6. Hitna pravila

- Ne deliti PIN, lozinku, token ili API ključ.
- Kada zaposleni ode, vlasnik/administrator odmah bira **Ukloni pristup** u OpsNest timu.
- Ako je pogrešno izdata faktura: ne brisati je; koristiti ispravku, formalno odobrenje ili storno sa razlogom.
- Ako je nacrt pogrešan: vratiti na doradu sa komentarom ili obrisati samo nacrt.
- Ako sinhronizacija prijavi konflikt: ne raditi paralelne ručne kopije. Zaustaviti unos na drugom uređaju, preuzeti poslednju reviziju i prijaviti incident administratoru.
- Ako nedostaje dokument za plaćanje: obaveza ne ide na plaćanje dok se dokument ne priloži i odobri.
- OpsNest tehnički blokira odobravanje i povezivanje bankovnog odliva za obavezu bez originalnog priloga ili povezanog ulaznog dokumenta projekta.
- OpsNest tehnički blokira i samoodobravanje: pripremilac fakture ili obaveze ne može biti njen odobravalac kada dokument prolazi kroz kontrolisani tok. Vlasnik zato mora imati aktivnog administratora-zamenika za odsustva i izuzetke.

## 7. Kontrolna tabla za preuzimanje smene

Osoba koja preuzima rad otvara ovim redom:

1. **Dashboard → Operativni centar** — jedinstveni red: odobrenja, vraćeni nacrti, banka, dospeća, dokumenti i budžetski izuzeci.
2. **Odobrenja** — fakture na čekanju i vraćene nacrte.
3. **Finansije** — otvorene obaveze, plan plaćanja, cash-flow, finansijski audit.
4. **Banka** — nepovezani prilivi/odlivi i stanje računa.
5. **Firma i projekti** — ugovori, avansi, budžeti, dokumenti i profit projekta.
6. **Finansije → Mesečna kontrola** — status deset obaveznih kontrola; ceo kalendarski mesec se ne može zaključiti dok nisu završene, audit lanac nije ispravan i svež backup nije potvrđen.
7. **Backup / OpsNest tim** — poslednja sinhronizacija i zamenik sa aktivnim pristupom.

Ako bilo koja tačka nije jasna, ne nagađati: upisati komentar, označiti izuzetak i predati vlasniku ili glavnom knjigovođi na proveru.
