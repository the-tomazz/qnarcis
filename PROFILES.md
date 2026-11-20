# QNarcIS – profili in prevodi / profiles and translations

- [Slovenščina](#profili-in-prevodi)
- [English](#profiles-and-translations)

## Profili in prevodi

### Kaj je profil

Profil določa, s katero infrastrukturo vtičnik deluje: katalog slojev, prijavo, pošiljanje slojev, iskalnik, obvestila, jezik vmesnika in privzete podlage. Vsak profil je samostojna konfiguracija za eno okolje oziroma skupino uporabnikov.

Vtičnik ima tri vgrajene profile:

| Profil | Katalog | Jezik | Funkcionalnosti |
|---|---|---|---|
| ARSO NarcIS - Slovenija | SI | slovenščina | vse |
| ARSO NarcIS - Severna Makedonija | MK | angleščina | katalog slojev, NarcIS karta, pomoč |
| ARSO NarcIS - Kosovo | XK | angleščina | katalog slojev, NarcIS karta, pomoč |

Uporabniki prejšnjih različic, ki so imeli izbrano državo MK ali XK, ob nadgradnji samodejno dobijo ustrezni profil. Izbire ni treba ponoviti.

### Izbira profila

1. Odprite **Nastavitve** (ikona zobnika).
2. V skupini **Profil infrastrukture** izberite profil. Pod seznamom so prikazani organizacija, koda kataloga, strežniki in vklopljene funkcionalnosti.
3. Po želji izberite privzete podlage. Ko izberete profil, ki ni aktiven, vtičnik naloži njegov katalog, da lahko izberete njegove podlage. Nad drevesom je prikazano stanje nalaganja. Če katalog ni dosegljiv (npr. strežnik je v vzdrževanju), je prikazan razlog in uporabljene so privzete podlage profila.
4. Potrdite z **OK** in ponovno zaženite QGIS.

Ob menjavi profila se odjavite iz zaščitenih funkcij, ker poverilnice ene infrastrukture ne smejo oditi na drugo. Katalog slojev novega profila se namesti ob ponovnem zagonu.

### Katalog slojev iz datoteke

Če povezava do kataloga na strežniku ne deluje, lahko katalog (`*.tar.xz`), ki ste ga dobili drugače, naložite iz datoteke:

1. V **Nastavitvah** izberite profil, za katerega je katalog.
2. Kliknite **Naloži katalog iz datoteke…** nad drevesom slojev in izberite datoteko. Vtičnik preveri arhiv in prikaže njegove sloje, da lahko izberete privzete podlage.
3. Potrdite z **OK** in ponovno zaženite QGIS.

Če je indeks katalogov dosegljiv, se datoteka shrani kot trenutni katalog profila. Ko strežnik objavi novejši katalog, ga vtičnik prenese kot običajno. Če indeks ni dosegljiv, se katalog namesti takoj, ob prvem zagonu z dosegljivim strežnikom pa ga zamenja katalog s strežnika. Izvirna datoteka ostane nespremenjena.

Datoteka, ki je bila ročno skopirana v mapo `_cached_qgz` vtičnika pod imenom `<koda>_<številka>.tar.xz` (npr. `MK_762.tar.xz`), še naprej deluje.

### Uvoz, izvoz in odstranitev

- **Uvozi…** naloži profil iz datoteke `*.qnarcis.json`. Pred uvozom se prikaže okno s strežniki, s katerimi se bo vtičnik povezoval, in z naslovom, kamor bosta poslana uporabniško ime in geslo. **Uvažajte samo profile iz zaupanja vrednega vira.** Ponoven uvoz profila z istim `id` obstoječi profil posodobi.
- **Izvozi…** shrani izbrani profil v datoteko. Izvoz vgrajenega profila je dobra predloga za lasten profil.
- **Odstrani** izbriše uvoženi profil. Vgrajenih profilov ni mogoče odstraniti.

Spremembe se shranijo šele, ko potrdite Nastavitve z **OK**.

### Nastavitve profila

Z gumbom **Nastavitve profila…** lahko spremenite posamezne vrednosti izbranega profila, ne da bi urejali datoteko. Izvorni JSON ostane nespremenjen, spremembe pa se shranijo ločeno.

- Zavihek **Osnovno**: ime profila, jezik, koda kataloga, osnovni URL ter vklop in API naslovi posameznih funkcionalnosti. Tukaj lahko vklopite tudi funkcionalnost, ki je izvorni profil ne vsebuje.
- Zavihek **Napredno**: preslikave imen polj v API klicih, uporabniške vloge, koordinatni sistemi, sloji iskalnika, privzete podlage, portal in logotip ter prevodi.
- Vrednost uredite z dvoklikom v stolpcu *Vrednost*. Stolpec *Stanje* pokaže *Privzeto*, *Spremenjeno* ali *Neveljavno*. Neveljavnega profila ni mogoče shraniti; razlog je izpisan pod seznamom.
- **Povrni izbrano** in **Povrni celoten profil na izvorni JSON** razveljavita spremembe.

Spremembe veljajo po ponovnem zagonu QGIS-a.

### Lasten profil

Najlažje je izvoziti vgrajeni profil in ga prilagoditi:

- `id` mora biti enoličen (male črke, številke, `.`, `_`, `-`), `name` je ime, ki ga vidijo uporabniki.
- `catalog_code` je vrednost polja `drzava` v indeksu katalogov, `language` pa jezik vmesnika.
- `services` vsebuje funkcionalnosti. Funkcionalnost, ki je profil ne navede, je izklopljena.
- Dovoljeni so samo naslovi `https://`.

Najmanjši profil vsebuje samo katalog:

```json
{
  "schema_version": 1,
  "id": "primer-katalog",
  "name": "Primer – samo katalog",
  "language": "en",
  "catalog_code": "HR",
  "services": {
    "catalog": { "index_url": "https://example.org/qnarcis/index.json" }
  },
  "default_layers": [["OpenStreetMap"]]
}
```

Indeks katalogov je lahko statična datoteka JSON v obliki ARSO indeksa:

```json
{ "items": [
  { "id": 3, "drzava": "HR", "povezava": "https://example.org/qnarcis/HR_3.tar.xz", "created": "2026-11-10T00:00:00Z" }
] }
```

Za lastno gostovanje kataloga je treba v profilu spremeniti samo `services.catalog.index_url`.

### Prevodi

Jezik vmesnika določa nastavitev `language` v profilu (npr. `sl`, `en`, `mk`). Za vsako besedilo vtičnik uporabi prvi razpoložljivi prevod:

1. `translations.<jezik>` v profilu,
2. datoteka `translations.json` v vtičniku,
3. angleški prevod,
4. slovensko izvirno besedilo.

Prevod v nov jezik:

1. Vir vseh besedil je `translations.json` v mapi vtičnika. Ključ je slovensko izvirno besedilo, `en` pa angleški prevod.
2. Prevajalec vsakemu vnosu doda prevod v svoj jezik, npr. `"mk": "…"`. Oznake, kot sta `{}` in `{datum}`, morajo ostati nespremenjene.
3. Prevode lahko uporabite takoj, brez nove izdaje vtičnika, tako da jih dodate v profil:

   ```json
   "language": "mk",
   "translations": {
     "mk": {
       "Izbirnik slojev": "Избирач на слоеви",
       "Nastavitve": "Поставки"
     }
   }
   ```

   Za trajno vključitev v vtičnik pošljite dopolnjeno datoteko `translations.json` skrbniku vtičnika.

Besedila, ki niso prevedena, so prikazana v angleščini, zato je prevod mogoče dopolnjevati postopoma.

Lokalna pomoč se prikaže iz mape `docs/<jezik>/index.html`, če obstaja (slovenščina: `docs/si`); sicer se prikaže angleška pomoč. Imena slojev in metapodatki prihajajo iz kataloga slojev in so v jeziku, v katerem je katalog pripravljen.

---

## Profiles and translations

### What is a profile

A profile defines which infrastructure the plugin works with: the layer catalog, login, layer upload, search, news, the interface language and the default base layers. Each profile is a self-contained configuration for one environment or group of users.

The plugin includes three built-in profiles:

| Profile | Catalog | Language | Features |
|---|---|---|---|
| ARSO NarcIS - Slovenija | SI | Slovenian | all |
| ARSO NarcIS - Severna Makedonija | MK | English | layer catalog, NarcIS map, help |
| ARSO NarcIS - Kosovo | XK | English | layer catalog, NarcIS map, help |

Users of earlier versions who had MK or XK selected as their country automatically get the matching profile after upgrading. There is no need to select it again.

### Selecting a profile

1. Open **Settings** (gear icon).
2. In the **Infrastructure profile** group, select a profile. The organization, catalog code, servers and enabled features are shown below the list.
3. Optionally choose the default base layers. When you select a profile that is not active, the plugin loads its catalog so you can pick its base layers. The loading status is shown above the tree. If the catalog cannot be reached (for example, the server is under maintenance), the reason is shown and the profile's default base layers are used.
4. Confirm with **OK** and restart QGIS.

Switching profiles logs you out of the protected features, because credentials for one infrastructure must never be sent to another. The new profile's layer catalog is installed when QGIS restarts.

### Layer catalog from a file

If the catalog link on the server does not work, you can load a catalog (`*.tar.xz`) that you obtained in another way from a file:

1. In **Settings**, select the profile the catalog belongs to.
2. Click **Load catalog from file…** above the layer tree and choose the file. The plugin checks the archive and shows its layers so you can pick the default base layers.
3. Confirm with **OK** and restart QGIS.

If the catalog index is reachable, the file is stored as the profile's current catalog. When the server publishes a newer catalog, the plugin downloads it as usual. If the index is not reachable, the catalog is installed immediately and replaced by the server catalog at the first start when the server is reachable. The original file is left unchanged.

A file copied manually into the plugin's `_cached_qgz` folder as `<code>_<number>.tar.xz` (for example `MK_762.tar.xz`) still works.

### Import, export and remove

- **Import…** loads a profile from a `*.qnarcis.json` file. Before importing, a dialog lists the servers the plugin will connect to and where your username and password will be sent. **Only import profiles from a trusted source.** Importing a profile with the same `id` again updates the existing profile.
- **Export…** saves the selected profile to a file. Exporting a built-in profile is a good starting point for your own profile.
- **Remove** deletes an imported profile. Built-in profiles cannot be removed.

Changes are saved only when you confirm Settings with **OK**.

### Profile settings

Use **Profile settings…** to change individual values of the selected profile without editing the file. The source JSON stays unchanged; your changes are stored separately.

- **Basic** tab: profile name, language, catalog code, base URL, and the on/off switch and API addresses of each feature. You can also enable a feature that the source profile does not include.
- **Advanced** tab: field name mappings for API calls, user roles, coordinate reference systems, search layers, default base layers, portal and logo, and translations.
- Double-click the *Value* column to edit a value. The *Status* column shows *Default*, *Changed* or *Invalid*. An invalid profile cannot be saved; the reason is shown below the list.
- **Restore selected** and **Restore the entire profile to the source JSON** undo your changes.

Changes take effect after restarting QGIS.

### Your own profile

The easiest way is to export a built-in profile and adapt it:

- `id` must be unique (lowercase letters, digits, `.`, `_`, `-`); `name` is the name users see.
- `catalog_code` is the value of the `drzava` field in the catalog index; `language` is the interface language.
- `services` lists the features. Any feature the profile does not list is disabled.
- Only `https://` addresses are allowed.

The smallest profile contains only the catalog:

```json
{
  "schema_version": 1,
  "id": "example-catalog",
  "name": "Example – catalog only",
  "language": "en",
  "catalog_code": "HR",
  "services": {
    "catalog": { "index_url": "https://example.org/qnarcis/index.json" }
  },
  "default_layers": [["OpenStreetMap"]]
}
```

The catalog index can be a static JSON file in the ARSO index format:

```json
{ "items": [
  { "id": 3, "drzava": "HR", "povezava": "https://example.org/qnarcis/HR_3.tar.xz", "created": "2026-11-10T00:00:00Z" }
] }
```

To host the catalog yourself, change only `services.catalog.index_url` in the profile.

### Translations

The interface language is set by `language` in the profile (for example `sl`, `en`, `mk`). For each text, the plugin uses the first available translation:

1. `translations.<language>` in the profile,
2. the plugin's `translations.json` file,
3. the English translation,
4. the original Slovenian text.

To translate into a new language:

1. The source of all texts is `translations.json` in the plugin folder. Each key is the original Slovenian text, and `en` is its English translation.
2. The translator adds a translation in their language to each entry, for example `"mk": "…"`. Placeholders such as `{}` and `{datum}` must be kept unchanged.
3. You can use the translations right away, without a new plugin release, by adding them to the profile:

   ```json
   "language": "mk",
   "translations": {
     "mk": {
       "Izbirnik slojev": "Избирач на слоеви",
       "Nastavitve": "Поставки"
     }
   }
   ```

   To include the translation in the plugin permanently, send the completed `translations.json` to the plugin maintainer.

Texts that are not translated are shown in English, so a translation can be completed gradually.

Local help is shown from `docs/<language>/index.html` if that folder exists (Slovenian: `docs/si`); otherwise the English help is shown. Layer names and metadata come from the layer catalog and are in the language the catalog was prepared in.
