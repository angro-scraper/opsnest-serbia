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

Kandidat za objavu. Javni update manifest ostaje na prethodnoj verziji dok novi instalater ne bude postavljen i javno preuzet sa istim SHA-256.
