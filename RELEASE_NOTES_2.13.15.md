# OpsNest 2.13.15

## Automatsko podešavanje firme

- Izbor države odmah podešava podrazumevanu valutu, standardnu PDV stopu i
  automatski tok e-fakture za novu firmu.
- Za Srbiju izbor važeće četvorocifrene KD 2010 šifre automatski bira
  odgovarajući poslovni profil (na primer digitalne usluge, građevina,
  trgovina ili transport).
- Forma jasno prikazuje šta je automatski pripremljeno i čuva ručnu potvrdu
  pravne forme i PDV statusa, jer se te činjenice ne mogu zaključiti samo iz
  delatnosti.
- Nova faktura po pravilu počinje na jeziku izvoza zemlje: srpski za Srbiju,
  bugarski za Bugarsku, nemački za Nemačku/Austriju i engleski za ostale
  trenutno podržane pakete. Jezik ostaje izmenjiv za svaku fakturu.

## Quality gate

- Python compilation and all 25 critical workflow tests passed.
