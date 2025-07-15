# bmm parlament scraper

A [Figyuszhoz](https://figyusz.k-monitor.hu) készült scraper, ami a parlament.hu-n megjelenő [irományok](https://www.parlament.hu/web/guest/iromanyok-lekerdezese) közt keres kulcsszavakat vagy adatfrissítést.

Dropdown menüből kiválasztható szűrőket használ.

A szűrők beállításainak sémáját az `options_schema.json` fájlban találod, amit a db-ben `options_schema`-nak kell beállítani az `eventgenerators` táblában. (a konkrét json fájlt nem használja semmit, csak azért van itt, hogy ne csak az adatbázisban legyen meg)

A scraper a parlament [XML API](https://www.parlament.hu/w-api-tajekoztato)-ját használja.

A forráskód a [Kormány scraper](https://github.com/Code-for-Hungary/bmm-kormanyscraper)-en alapszik, ami meg a [Közlöny scraper](https://github.com/Code-for-Hungary/bmm-kozlonyscraper)-re alapszik.
