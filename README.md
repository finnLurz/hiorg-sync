## Motivation / Warum HiOrg-Sync?

HiOrg-Sync schließt die typische Lücke zwischen **fachlicher Datenpflege in HiOrg** und dem **technischen Zielsystem Active Directory / LDAP**. In vielen Organisationen ist das AD/LDAP der zentrale „Identity Backbone“: daran hängen **Logins**, **Berechtigungen**, **Gruppen**, **Verteiler**, **Telefonbücher** und zahlreiche **Fachanwendungen**. Wenn HiOrg und AD auseinanderlaufen, entstehen schnell veraltete Daten, manuelle Doppelpflege und Sicherheitsrisiken durch zu lange bestehende Zugriffe.

HiOrg-Sync macht HiOrg zur führenden Quelle (**„Source of Truth“**) für Personen- und Gruppenzuordnungen und sorgt dafür, dass das AD/LDAP **automatisiert**, **konsistent** und **nachvollziehbar** aktuell bleibt.

---

## Was ihr damit gewinnt

### Einheitlicher Datenstand (Single Source of Truth)
Kontaktdaten (E-Mail, Telefon), Status (aktiv/inaktiv), Standort/OV und ggf. weitere Attribute bleiben im AD/LDAP konsistent zu HiOrg. So stimmen Telefonbuch, Outlook/GAL und systemübergreifende Verzeichnisse.

### Automatische Gruppenpflege (der größte Hebel)
AD-Gruppen dienen häufig als Grundlage für:

- Zugriffe auf Dateien/SharePoint/Nextcloud
- Rollen in Anwendungen
- Mailinglisten/Verteiler
- Berechtigungen in Fachsystemen

HiOrg-Sync sorgt dafür, dass Gruppenmitgliedschaften automatisch hinzugefügt/entfernt werden – basierend auf HiOrg-Gruppen und Standortlogik.

### Weniger manueller Aufwand, weniger Fehler
Keine Doppelpflege in HiOrg und AD. Weniger „Bitte füge X in Gruppe Y“-Anfragen. Weniger Tippfehler, weniger Vergessen.

### Mehr Sicherheit & Sauberkeit
Berechtigungen folgen dem fachlichen Status. Wenn jemand in HiOrg nicht mehr aktiv ist oder die Zugehörigkeit wechselt, laufen Gruppen und Zugriffe nicht „versehentlich“ weiter.

### Skalierbar über mehrere OVs / Standorte
HiOrg-Sync behandelt OVs sauber getrennt (Tokens, Marker, Mapping pro OV) und bildet Standorte kontrolliert auf eure OU- und Gruppenstruktur im AD ab.

### UI statt Konfig-Hölle
Abweichungen zwischen HiOrg-Gruppenname und AD-Konvention (Umlaute, Präfixe, historische Namen) löst ihr bequem über die UI:

- pro Standort ein `GroupBaseDN`
- pro HiOrg-Gruppe optional ein `CN-Override` (wenn AD-Gruppe anders heißt)

### Optional: Benachrichtigungen bei Kontaktänderungen (SMTP)
Bei Änderungen an Kontaktdaten kann HiOrg-Sync (je nach Implementierung) Benachrichtigungen an einen Empfängerkreis senden – z. B. IT/Administration oder eine definierte Funktionsmailbox.

---

## Typische Probleme ohne HiOrg-Sync (und wie es besser wird)

### Ohne Automatisierung
- Telefon-/Maildaten im AD sind veraltet → Telefonbuch/GAL stimmt nicht
- Personen sind in AD-Gruppen, obwohl sie fachlich nicht mehr dazugehören
- Neue Mitglieder werden in mehreren Systemen separat angelegt → Verzögerung/Fehler
- Standortwechsel führt zu „Gruppen-Wildwuchs“ und inkonsistenten Berechtigungen

### Mit HiOrg-Sync
- HiOrg ist die fachliche Wahrheit → AD spiegelt diese Wahrheit regelmäßig
- Gruppenmitgliedschaften folgen den HiOrg-Zuordnungen → Berechtigungen sind „fachlich begründet“
- Standortlogik wird systematisch abgebildet → OU-/Gruppenstruktur bleibt sauber

---

## Leitprinzipien

### HiOrg ist führend, AD/LDAP ist Zielsystem
HiOrg enthält die fachlich korrekten Informationen. AD/LDAP soll diese Informationen technisch abbilden.

### Inkrementell statt „jedes Mal Vollsync“
HiOrg-Sync arbeitet über einen Marker (`updated_since`) und verarbeitet pro Lauf nur Änderungen seit dem letzten Lauf. Das reduziert:

- Laufzeit
- API-Last
- LDAP-Operationen

…und erhöht die Betriebssicherheit.

### Kontrollierbarkeit und Transparenz
Änderungen sind über Dry-Run und Logs nachvollziehbar; Sonderfälle im Gruppenmapping lassen sich gezielt per UI lösen.
