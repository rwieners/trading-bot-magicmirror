#!/usr/bin/env python3
"""
Entfernt alle Trades aus der Datenbank, die den Status 'CLOSED_MANUAL_SYNC' oder reason 'SYNC_KRAKEN' haben.
"""
import sqlite3
from pathlib import Path

db_path = Path(__file__).parent.parent / 'logs' / 'trades.db'

conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

# Lösche alle manuell geschlossenen und alle alten SYNC_KRAKEN-Trades
cursor.execute("DELETE FROM trades WHERE status = 'CLOSED_MANUAL_SYNC' OR reason = 'SYNC_KRAKEN'")
conn.commit()

print("Bereinigung abgeschlossen.")

conn.close()
