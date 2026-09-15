# OpsNest 2.13.18 — vraćanje unosa stavki posle avansa

## Ispravka

Pri promeni avansnog računa u standardni ili završni račun, Tk forma je pokušavala da prikaže brzi unos ispred još skrivene trake komandi. Greška `window isn't packed` prekidala je osvežavanje i ostavljala sakriven unos i zastarelu tabelu/zbirove. Traka se sada vraća pre brzog unosa, pa ostatak osvežavanja normalno završava.

- Vraćaju se polja i dugmad za dodavanje stavki.
- Osvežavaju se prikaz stavki i ukupni iznosi.
- Postojeće ručno unete stavke i nesačuvan sadržaj brzog unosa ostaju očuvani pri prelasku između vrsta računa.
- Avans ostaje odvojen od rada i materijala; pravila čuvanja, odobravanja, izdavanja i pristupa nisu menjana.
- Nema migracije baze, promene originalnih šablona, naloga ili korisnikovih poslovnih podataka.

## Provere

- Pre ispravke, novi Tk regresioni testovi ponavljaju istu grešku.
- Posle ispravke, sva 4 nova testa prolaze: početni standardni račun, povratak iz avansa i dodavanje stavke sa tačnim zbirom, ponovljeni prelazi standardni/avansni/završni na svih 5 jezika uz očuvanje unosa, i izlazak iz avansa bez izabranog projekta.
- Svih 55 desktop testova prošlo je na Windows / Python 3.13, uz izolovane testne podatke.
- Objedinjena desktop/cloud provera: 81/81 test prolazi (45,4 s); web portal: 5/5 JavaScript testova prolazi. Novi Tk testovi uključeni su u Windows/Linux CI (bez dostupnog ekrana preskaču se samo Tk testovi).

## Status objave

- Instalater je postavljen na `https://opsnestone.com/downloads/OpsNest-Setup-2.13.18.exe` i ponovo preuzet sa javnog sajta; veličina i SHA-256 identični su lokalnom instalateru.
- Veličina: `114780896` bajtova.
- SHA-256: `2b81fa3b77b5fc110e46ebfc2578ad606c30dfccf50d5d3baaa0cf9d8888241b`.
- Proveren sadržaj instalatera: uključena ispravljena aplikacija 2.13.18, bez poslovne baze i privatnog šablona. Četiri Tk testa prolaze i nad metodom iz ugrađenog EXE-a.
- CI provera izvornog koda `1535e2d` uspešna je na Windows-u i Ubuntu-u (run `34944791875`).
- Javni manifest se prebacuje na ovaj prethodno provereni fajl. Instalater nema Authenticode potpis izdavača; aplikacija pre instalacije proverava SHA-256.
- Otvorena korisnikova aplikacija nije zatvarana niti automatski ažurirana; instalaciju korisnik pokreće nakon čuvanja rada.
