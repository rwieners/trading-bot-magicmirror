import sqlite3

DB_PATH = '/Users/rene/dev/Broker/logs/trades.db'

def remove_zero_positions():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Lösche alle offenen Trades mit entry_size <= 0 oder entry_value <= 0
    cursor.execute('''
        DELETE FROM trades WHERE (status = 'OPEN' OR status IS NULL) AND (entry_size <= 0 OR entry_value <= 0)
    ''')
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    print(f"Entfernt: {deleted} Null-Positionen aus der Datenbank.")

if __name__ == '__main__':
    remove_zero_positions()
