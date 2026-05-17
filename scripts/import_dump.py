import json
import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from tqdm import tqdm
from datetime import datetime
import bz2

# Add to path eddn_bot/core directory root folder
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core import get_connection, setup_db, upsert_conflict
from core import extract_relevant_conflicts

# ---- CONFIGS ----
# Factions target
TARGET_FACTIONS = ["MCC 445 Services", "Nat9481 Nobles", "Galileo Corporation"]

# Directory Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "conflicts.db")

# URL Base Archive
EDDN_ARCHIVE_URL = "https://edgalaxydata.space/EDDN/"
TIMEOUT = 10

# Make folder if it doesn't exist
try:
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        print(f"Folder created: {DATA_DIR}")
except OSError as e:
    print(f"Error while making folder {DATA_DIR}: {e}")
    sys.exit(1)


# ---- FUNCTIONS ----
def download_dumps():
    """
    Find and download Journal.Location, Journal.FSDJump, Journal.CarrierJump
    .bz2 and .jsonl files of the current month inside the archive
    """

    # Build URL based on datetime.now()
    current_ym = datetime.now().strftime("%Y-%m")
    target_url_bz2 = urljoin(EDDN_ARCHIVE_URL, f"{current_ym}/")
    target_url_jsonl = EDDN_ARCHIVE_URL 
    
    try:
        print(f"Connecting to {target_url_bz2}...")
        response_bz2 = requests.get(target_url_bz2, timeout=TIMEOUT)
        response_bz2.raise_for_status()
    except requests.exceptions.Timeout: 
        print("Error: Connection expired (Timeout)")
        return None
    except Exception as e:
        print(f"Connection error to {target_url_bz2}: {e}")
        print("Current month archive maybe it's not avaiable yet.")
        return None

    try:
        print(f"Connecting to {target_url_jsonl}...")
        response_jsonl = requests.get(EDDN_ARCHIVE_URL, timeout=TIMEOUT)
        response_jsonl.raise_for_status()
    except requests.exceptions.Timeout: 
        print("Error: Connection expired (Timeout)")
        return None
    except Exception as e:
        print(f"Connection error to {target_url_jsonl}: {e}")
        print("Current .jsonl maybe it's not avaiable yet.")
        return None
    
    soup_bz2 = BeautifulSoup(response_bz2.text, 'html.parser')
    links_bz2 = soup_bz2.find_all('a')

    soup_jsonl = BeautifulSoup(response_jsonl.text, 'html.parser')
    links_jsonl = soup_jsonl.find_all('a')
    

    # Filter for Journal.Location, Journal.FSDJump, Journal.CarrierJump (exclunding test)
    location_files_bz2 = [
        link.get('href') for link in links_bz2 
        if link.get('href') 
        and(
            'Journal.Location' in link.get('href') 
            or 'Journal.FSDJump' in link.get('href')
            or 'Journal.CarrierJump' in link.get('href')
        )
        and link.get('href').endswith('.bz2')
        and 'Test' not in link.get('href')
    ]

    location_files_jsonl = [
        link.get('href') for link in links_jsonl
        if link.get('href') 
        and(
            'Journal.Location' in link.get('href') 
            or 'Journal.FSDJump' in link.get('href')
            or 'Journal.CarrierJump' in link.get('href')
        )
        and link.get('href').endswith('.jsonl')
        and 'Test' not in link.get('href')
    ]

    if not location_files_bz2:
        print(f"No .bz2 Journal files found in {target_url_bz2}")
        return None

    if not location_files_jsonl:
        print(f"No .jsonl Journal files found in {target_url_jsonl}")
        return None
        
    location_files = location_files_bz2 + location_files_jsonl
    
    # Sort by most recent
    location_files.sort(reverse=True)
    print(f"Found {len(location_files)} files to download.") 

    for file_name in location_files:
        dump_file_path = os.path.join(DATA_DIR, file_name)
        if file_name.endswith('.bz2'):
            download_url = urljoin(target_url_bz2, file_name)
        elif file_name.endswith('.jsonl'):
            download_url = urljoin(target_url_jsonl, file_name)
    
        # Skip download if local file has same size of remote file or already
        # i.e. same file already present in /data folder 
        head_resp = requests.head(download_url)
        remote_size = int(head_resp.headers.get('content-length', 0))

        if os.path.exists(dump_file_path):
            local_size = os.path.getsize(dump_file_path)
            if local_size == remote_size:
                print(f"File already present: skipping download -> {file_name}")
                continue
            else:
                print(f"Update found for {file_name} (local: {local_size}B -> remote: {remote_size}B)")
        else:
            print(f"Downloading: {file_name}...")

        with requests.get(download_url, stream=True) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            
            with open(dump_file_path, 'wb') as f, tqdm(
                desc=file_name,
                total=total_size,
                unit='iB',
                unit_scale=True,
                unit_divisor=1024,
            ) as bar:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    bar.update(len(chunk))
    return location_files

def elaborate_data(conn):
    print("Importing into the database...")

    # Dictionary only to keep most recent event
    # Keys: (system_name, fazione_A, fazione_B)
    latest_conflicts = {}

    for elem in os.scandir(DATA_DIR):
        # Elaborate .bz2 and .jsonl files in one block
        if elem.is_file() and 'Journal' in elem.name and (elem.name.endswith('.bz2') or elem.name.endswith('.jsonl')):
            print(f"Elaborating file: {elem.name}...")
            lines_read = 0
            
            opener = bz2.open if elem.name.endswith('.bz2') else open

            with opener(elem.path, 'rt', encoding='utf-8') as f:
                for line in f:
                    lines_read += 1
                    try:
                        data = json.loads(line)
                        
                        if data.get('$schemaRef') == "https://eddn.edcd.io/schemas/journal/1":
                            conflicts = extract_relevant_conflicts(data, TARGET_FACTIONS)
                            
                            for c in conflicts:
                                # Sort factions in order to get an unic key
                                f_a, f_b = sorted([c['faction_1'], c['faction_2']])
                                key = (c['system'], f_a, f_b)
                                
                                # Messages from EDDN have timestamp ISO 8601 (e.g. 2024-05-14T12:00:00Z)
                                # compare them as string
                                # If key does not exit or current timestamp is greater than saved timestamp -> update
                                if key not in latest_conflicts or c['timestamp'] > latest_conflicts[key]['timestamp']:
                                    latest_conflicts[key] = c
                    except (json.JSONDecodeError, KeyError):
                        continue

                    if lines_read % 100000 == 0:
                        print(f" -> {lines_read} rows have been read from {elem.name}...")

    print("Saving most recent conflicts in database...DONE")
    conflicts_inserted = 0

    # Now we query DB with only most recent conflicts
    for c in latest_conflicts.values():
        upsert_conflict(
            conn, c['system'], c['faction_1'], c['faction_2'],
            c['war_type'], c['status'], c['f1_days_won'], 
            c['f2_days_won'], c['stake1'], c['stake2'], 
            c['timestamp'], "DUMP"
            )
        conflicts_inserted += 1
                
    print(f"FINISHED! total conflicts imported/updated: {conflicts_inserted}")

def clean_old_dumps(all_valid_files):
    print("Checking for old dumps to delete...")
    deleted_count = 0
    for elem in os.scandir(DATA_DIR):
        if elem.is_file() and 'Journal' in elem.name:
            if elem.name not in all_valid_files:
                os.remove(elem.path)
                print(f"Deleted old dump: {elem.name}")
                deleted_count += 1
    
    if deleted_count == 0:
        print("No old dumps found. Directory is clean!")

def delete_dumps(all_valid_files):
    for file_name in all_valid_files:
        dump_file_path = os.path.join(DATA_DIR, file_name)
        if os.path.exists(dump_file_path):
            try:
                os.remove(dump_file_path)
                print(f"Removed elaborated dump from /data: {file_name}")
            except OSError as e:
                print(f"Error while deleting {file_name}: {e}")
    print("Directory /data cleaned!")

def process_dumps():
    valid_files = download_dumps()
    
    if valid_files is None:
        print("Downloading failed. Import aborted.")
        return

    # Substitute None with empty lists to avoid errors
    valid_files = valid_files or []

    clean_old_dumps(valid_files)    

    conn = get_connection(DB_PATH)
    setup_db(conn)
    elaborate_data(conn)
    conn.close()

    delete_dumps(valid_files)

if __name__ == "__main__":
    process_dumps()
