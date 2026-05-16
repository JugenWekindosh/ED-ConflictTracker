import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core import get_connection, print_all_conflicts, cleanup_old_conflicts

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "conflicts.db")

if __name__ == "__main__":
    conn = get_connection(DB_PATH)
    cleanup_old_conflicts(conn, days=7)
    print_all_conflicts(conn)
