import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "conflicts.db")



def get_connection(db_path=DB_PATH):
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



def get_conflicts(conn):
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM conflicts')
        conflicts_list = cursor.fetchall()
        if not conflicts_list:
            print("[DB] Conflicts table empty!")
            return []
        return conflicts_list
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            print("[DB] Error in get_conflicts: table does not exist")
            return []
        else:
            print(f"[DB] Unexpected sqlite3 error in get_conflicts: {e}")
            return []
    except Exception as e:
        print(f"[DB] Unexpected error in get_conflicts: {e}")
        return []



def print_database(conn, active_flag=False):
    cursor = conn.cursor()
    try:
        if active_flag == False:
            query = """
                SELECT
                    system_name AS Sistema,
                    faction_1 AS Fazione_1,
                    faction_2 AS Fazione_2,
                    status AS Stato,
                    is_active AS Attivo,
                    timestamp AS Message_Timestamp,
                    last_updated AS Ultimo_Aggiornamento,
                    source AS Source
                FROM conflicts
                ORDER BY is_active DESC, last_updated DESC
            """
        else:
            query = """
                SELECT
                    system_name AS Sistema,
                    faction_1 AS Fazione_1,
                    faction_2 AS Fazione_2,
                    status AS Stato,
                    is_active AS Attivo,
                    timestamp AS Message_Timestamp,
                    last_updated AS Ultimo_Aggiornamento,
                    source AS Source
                FROM conflicts
                WHERE is_active = 1
                ORDER BY is_active DESC, last_updated DESC
            """

        cursor.execute(query)
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

    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            print("[DB] Error in print_database: table does not exist")
        else:
            print(f"[DB] Unexpected sqlite3 error in print_database: {e}")

    except Exception as e:
        print(f"[DB] Unexpected error in print_database: {e}")



def upsert_conflict(conn, system_name, faction_1, faction_2, war_type, status, f1_days, f2_days, stake1, stake2, timestamp, source):
    cursor = conn.cursor()
    roma_tz = ZoneInfo("Europe/Rome")
    now = datetime.now(roma_tz).strftime('%Y/%m/%d - T: %H:%M:%S')

    # Sorts factions alphabetically 
    f_a, f_b = sorted([faction_1, faction_2])
    
    # exchange scores and assets if sorted alphabetically
    if f_a != faction_1:
        f1_days, f2_days = f2_days, f1_days
        stake1, stake2 = stake2, stake1

    #clean_status = status.strip() if status else ""
    is_active = 1 if 'active' in status else 0

    cursor.execute('''
        SELECT f1_days_won, f2_days_won, is_active, status FROM conflicts 
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
    ''', (system_name, f_a, f_b, war_type, status, f1_days, f2_days, stake1, stake2, timestamp, now, is_active, source))
    
    conn.commit()

    # 1) Check if conflict was not in the database
    if previous_state is None:
        if is_active == 0 and 'pending' in status:
            return "PENDING"
        if is_active == 1 and 'active' in status:
            return "NEW"
        if is_active == 0 and 'concluded' in status:
            return "CONCLUDED"
    
    # 2) Conditions if conflict already present in database
    if previous_state is not None:
        if previous_state['is_active'] == 0 and is_active == 1:
            if 'pending' in previous_state['status'] and 'active' in status:
                return "ACTIVATED"
            if 'concluded' in previous_state['status'] and 'active' in status:
                return "REACTIVATED"

        if is_active == 1 and (previous_state['f1_days_won'] != f1_days or previous_state['f2_days_won'] != f2_days):
            return "SCORE_CHANGE"

        if previous_state['is_active'] == 1 and is_active == 0:
            if 'pending' in status:
                return "PENDING"
            if 'concluded' in status:
                return "CONCLUDED"
    
    # 3) If no change from previous state 
    return "NO_CHANGE"



def cleanup_old_conflicts(conn, days=7):
    cursor = conn.cursor()
    threshold_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')

    try:
        cursor.execute('''
            SELECT DISTINCT system_name FROM conflicts WHERE
            timestamp < ? AND is_active = 1
        ''', (threshold_date,))

        deleted_systems = [row['system_name'] for row in cursor.fetchall()]
        if deleted_systems:
            cursor.execute('''
                DELETE FROM conflicts WHERE
                timestamp < ? AND is_active = 1
            ''', (threshold_date,))
            conn.commit()
            print(f"[DB] Cleaning complete: removed {len(deleted_systems)} obsolete conflicts ({', '.join(deleted_systems)})")
        
        else:
            print("[DB] No obsolete conflict found in database.")
        return deleted_systems
    
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            print("[DB] Error in cleanup_old_conflicts: table does not exist")
        else:
            print(f"[DB] Unexepcted sqlite3 error in cleanup_old_conflicts: {e}")
        return []
    except Exception as e:
        print(f"[DB] Errore durante la pulizia dei vecchi conflitti: {e}")
        return [] 




def cleanup_concluded_conflicts(conn):
    cursor = conn.cursor()

    try:
        cursor.execute('''
            SELECT DISTINCT system_name FROM conflicts WHERE
            is_active = 0 AND status = 'concluded'
        ''')

        deleted_systems = [row['system_name'] for row in cursor.fetchall()]
        if deleted_systems:
            cursor.execute('''
                DELETE FROM conflicts WHERE
                is_active = 0 AND status = 'concluded'
            ''')
            conn.commit()
            print(f"[DB] Cleaning complete: removed {len(deleted_systems)} concluded conflicts ({', '.join(deleted_systems)})")
        
        else:
            print("[DB] No concluded conflict found in database.")
        return deleted_systems

    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            print("[DB] Error in cleanup_concluded_conflicts: table does not exist")
        else:
            print(f"[DB] Unexepcted sqlite3 error in cleanup_concluded_conflicts: {e}")
        return []
    except Exception as e:
        print(f"[DB] Errore durante la pulizia dei conflitti conclusi: {e}")
        return []
