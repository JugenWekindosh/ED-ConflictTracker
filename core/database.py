import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

def get_connection(db_path='data/conflicts.db'):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row # Acces table's rows as dictionaries
    return conn

def setup_db(conn):
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conflicts (
            system_name TEXT,
            faction_1 TEXT,
            faction_2 TEXT,
            war_type TEXT,
            status TEXT,
            f1_days_won INTEGER,
            f2_days_won INTEGER,
            stake1 TEXT,
            stake2 TEXT,
            timestamp TEXT,
            last_updated DATETIME,
            is_active INTEGER DEFAULT 0,
            source TEXT,
            PRIMARY KEY (system_name, faction_1, faction_2)
        )
    ''')
    conn.commit()

def get_active_conflicts(conn):
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM conflicts WHERE is_active = 1')
    return cursor.fetchall()

def print_all_conflicts(conn):
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT
                system_name AS Sistema,
                faction_1 AS Fazione_1,
                faction_2 AS Fazione_2,
                status AS Stato,
                is_active AS Attivo,
                timestamp AS Message_Timestamp,
                source AS Source
            FROM conflicts
            ORDER BY is_active DESC, last_updated DESC
        """)
        rows = cursor.fetchall()

        if not rows:
            print("\n[DB] 'conflicts' table empty!")
            return

        # Intestazioni delle colonne
        headers = [description[0] for description in cursor.description]
        
        print("\n--- CONTENUTO SINTETICO DATABASE CONFLITTI ---")
        
        # Tentativo di usare tabulate per una grafica migliore
        try:
            from tabulate import tabulate
            print(tabulate(rows, headers=headers, tablefmt="grid"))
        except ImportError:
            # Formattazione manuale semplice se tabulate non è installato
            header_line = " | ".join(headers)
            print(header_line)
            print("-" * len(header_line))
            for row in rows:
                print(" | ".join(str(item) for item in row))
        
        print(f"--- Totale record: {len(rows)} ---\n")

    except Exception as e:
        print(f"[DB] Error in print_all_conflicts while printing database on console: {e}")


def print_active_conflicts(conn):
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT
                system_name AS Sistema,
                faction_1 AS Fazione_1,
                faction_2 AS Fazione_2,
                status AS Stato,
                is_active AS Attivo,
                timestamp AS Message_Timestamp,
                source AS Source
            FROM conflicts
            WHERE is_active = 1
            ORDER BY is_active DESC, last_updated DESC
        """)
        rows = cursor.fetchall()

        if not rows:
            print("\n[DB] 'conflicts' table empty!")
            return

        # Intestazioni delle colonne
        headers = [description[0] for description in cursor.description]
        
        print("\n--- CONTENUTO SINTETICO DATABASE CONFLITTI ATTIVI---")
        
        # Tentativo di usare tabulate per una grafica migliore
        try:
            from tabulate import tabulate
            print(tabulate(rows, headers=headers, tablefmt="grid"))
        except ImportError:
            # Formattazione manuale semplice se tabulate non è installato
            header_line = " | ".join(headers)
            print(header_line)
            print("-" * len(header_line))
            for row in rows:
                print(" | ".join(str(item) for item in row))
        
        print(f"--- Totale record: {len(rows)} ---\n")

    except Exception as e:
        print(f"[DB] Error in print_active_conflicts while printing database on console: {e}")



def upsert_conflict(conn, system_name, faction_1, faction_2, war_type, status, f1_days, f2_days, stake1, stake2, timestamp, source):
    cursor = conn.cursor()
    roma_tz = ZoneInfo("Europe/Rome")
    now = datetime.now(roma_tz).isoformat()

    # Sorts factions alphabetically 
    f_a, f_b = sorted([faction_1, faction_2])
    
    # exchange scores and assets if sorted alphabetically
    if f_a != faction_1:
        f1_days, f2_days = f2_days, f1_days
        stake1, stake2 = stake2, stake1

    clean_status = status.strip() if status else ""
    if clean_status == "":
        clean_status = "ended"
    is_active = 1 if clean_status.lower() in ['active'] else 0

    cursor.execute('''
        SELECT f1_days_won, f2_days_won, is_active FROM conflicts 
        WHERE system_name = ? AND faction_1 = ? AND faction_2 = ?
    ''', (system_name, f_a, f_b))
    
    previous_state = cursor.fetchone()

    # UPSERT data
    cursor.execute('''
        INSERT INTO conflicts (system_name, faction_1, faction_2, war_type, status, f1_days_won, f2_days_won, stake1, stake2, timestamp, last_updated, is_active, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(system_name, faction_1, faction_2) DO UPDATE SET
            war_type = excluded.war_type,
            status = excluded.status,
            f1_days_won = excluded.f1_days_won,
            f2_days_won = excluded.f2_days_won,
            stake1 = excluded.stake1,
            stake2 = excluded.stake2,
            timestamp = excluded.timestamp,
            last_updated = excluded.last_updated,
            is_active = excluded.is_active,
            source = excluded.source
    ''', (system_name, f_a, f_b, war_type, clean_status, f1_days, f2_days, stake1, stake2, timestamp, now, is_active, source))
    
    conn.commit()

    # return state result
    if previous_state is None:
        return "NEW"
    elif previous_state['is_active'] == 0:
        return "REACTIVATED"
    elif previous_state['f1_days_won'] != f1_days or previous_state['f2_days_won'] != f2_days:
        return "SCORE_CHANGE"
    
    return "NO_CHANGE"

def cleanup_old_conflicts(conn, days=7):
    cursor = conn.cursor()
    threshold_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')

    try:
        cursor.execute('''
            SELECT DISTINCT system_name FROM conflicts WHERE timestamp < ? AND is_active = 1
        ''', (threshold_date,))
        deleted_systems = [row['system_name'] for row in cursor.fetchall()]
        if deleted_systems:
            cursor.execute('''
                DELETE FROM conflicts WHERE timestamp < ? AND is_active = 1
            ''', (threshold_date,))
            conn.commit()
            print(f"[DB] Pulizia completata: rimossi {len(deleted_systems)} conflitti obsoleti ({', '.join(deleted_systems)})")
        else:
            print("[DB] Nessun conflitto obsoleto trovato nel database.")
        return deleted_systems
    except Exception as e:
        print(f"[DB] Errore durante la pulizia dei vecchi conflitti: {e}")
        return [] 
