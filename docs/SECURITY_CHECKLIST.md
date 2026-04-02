# SECURITY_CHECKLIST.md

## Sicherheits-Checkliste für trading-bot-magicmirror

### 1. API-Schlüssel & Secrets
- [ ] API-Keys niemals im Code oder öffentlichen Repos speichern
- [ ] Nutzung von .env-Dateien oder Umgebungsvariablen für Secrets
- [ ] config/user_settings.json und .env in .gitignore aufnehmen

### 2. Datei- und Prozessrechte
- [ ] Logs, Datenbanken und Konfigurationsdateien nur für Bot-User lesbar/schreibbar (chmod 600/700)
- [ ] Keine unnötigen Schreibrechte für das MagicMirror-Modul

### 3. CLI/JSON-Ausgabe
- [ ] Keine sensiblen Daten (API-Keys, Passwörter, interne Pfade) in CLI- oder JSON-Ausgabe
- [ ] Fehlerausgaben ohne Tracebacks mit sensiblen Pfaden/Variablen

### 4. Netzwerk & Schnittstellen
- [ ] REST-Endpoints (falls genutzt) nur auf localhost binden
- [ ] Keine offenen Ports ohne Authentifizierung

### 5. Datenbankzugriff
- [ ] SQLite-Datenbankdateien mit restriktiven Rechten versehen
- [ ] Keine SQL-Injection-Gefahr (keine externe Eingabe)

### 6. pm2 & Autostart
- [ ] pm2 als dedizierter User, nicht als root
- [ ] Startskripte schreiben keine sensiblen Daten in Logs

### 7. MagicMirror-Konfiguration
- [ ] Nur erlaubte Optionen in MagicMirror-Konfigurationsdatei übernehmen
- [ ] Keine Möglichkeit, über die Config beliebigen Code auszuführen

### 8. Abhängigkeiten
- [ ] requirements.txt regelmäßig auf CVEs prüfen (z.B. mit pip-audit)
- [ ] Keine unnötigen/veralteten Pakete installieren

### 9. Updates & Wartung
- [ ] Regelmäßige Updates des Bots und der Abhängigkeiten
- [ ] Monitoring für ungewöhnliche Aktivitäten (z.B. viele Fehlversuche, unerwartete API-Fehler)

---

Vor Deployment alle Punkte prüfen und abhaken!
