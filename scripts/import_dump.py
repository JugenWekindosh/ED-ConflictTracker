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
TARGET_FACTIONS = ["MCC 445 Services", "Galileo Corporation"]

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
def download_latest_dumps():
    """
    Find and download Journal.Location, Journal.FSDJump, Journal.CarrierJump
    .bz2 files of the current month inside the archive
    """

    # Build URL based on datetime.now()
    current_ym = datetime.now().strftime("%Y-%m")
    target_url = urljoin(EDDN_ARCHIVE_URL, f"{current_ym}/")
    
    try:
        print(f"Connecting to {target_url}...")
        response = requests.get(target_url, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.Timeout: 
        print("Error: Connection expired (Timeout)")
        return False
    except Exception as e:
        print(f"Connection error to {target_url}: {e}")
        print("Current month archive maybe it's not avaiable yet.")
        return False
    
    soup = BeautifulSoup(response.text, 'html.parser')
    links = soup.find_all('a')
    
    # Filter for Journal.Location, Journal.FSDJump, Journal.CarrierJump 
    # and extension .bz2 (exclunding test)
    location_files = [
        link.get('href') for link in links 
        if link.get('href') 
        and(
            'Journal.Location' in link.get('href') 
            or 'Journal.FSDJump' in link.get('href')
            or 'Journal.CarrierJump' in link.get('href')
        )
        and link.get('href').endswith('.bz2')
        and 'Test' not in link.get('href')
    ]
            
    if not location_files:
        print(f"No .bz2 Journal files found in {target_url}")
        return False
        
    # Sort by most recent
    location_files.sort(reverse=True)
    print(f"Found {len(location_files)} files to download.") 

    for file_name in location_files:
        download_url = urljoin(target_url, file_name)
        dump_file_path = os.path.join(DATA_DIR, file_name)
        
        # Skip download if local file has same size of remote file or already
        # i.e. same file already presenti in /data folder 
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

def download_latest_dump():
    """
    Find and download Journal.Location, Journal.FSDJump, Journal.CarrierJump
    .jsonl files in the base archive
    """

    try:
        print(f"Connecting to {EDDN_ARCHIVE_URL}...")
        response = requests.get(EDDN_ARCHIVE_URL, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.Timeout: 
        print("Error: Connection expired (Timeout)")
        return False
    except Exception as e:
        print(f"Connection error to {EDDN_ARCHIVE_URL}: {e}")
        print("Current .jsonl maybe it's not avaiable yet.")
        return False
    
    soup = BeautifulSoup(response.text, 'html.parser')
    links = soup.find_all('a')
    
    # Filter for Journal.Location, Journal.CarrierJump, Journal.FSDJump
    # and extension .jsonl (exclunding test)
    location_files = [
        link.get('href') for link in links 
        if link.get('href') 
        and(
            'Journal.Location' in link.get('href') 
            or 'Journal.FSDJump' in link.get('href')
            or 'Journal.CarrierJump' in link.get('href')
        )
        and link.get('href').endswith('.jsonl')
        and 'Test' not in link.get('href')
    ]
            
    if not location_files:
        print(f"No Journal .jsonl files found in {EDDN_ARCHIVE_URL}")
        return False
        
    # Sort by most recent
    location_files.sort(reverse=True)
    print(f"Found {len(location_files)} files to download.") 

    for file_name in location_files:
        download_url = urljoin(EDDN_ARCHIVE_URL, file_name)
        dump_file_path = os.path.join(DATA_DIR, file_name)
        
        # Skip download if local file has same size of remote file or already
        # i.e. same file already presenti in /data folder 
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
    conflicts_inserted = 0

    for elem in os.scandir(DATA_DIR):
        # Elaborate .bz2 files
        if elem.is_file() and 'Journal' in elem.name and elem.name.endswith('.bz2'):
            print(f"Elaborating file: {elem.name}...")
            lines_read = 0
            
            with bz2.open(elem.path, 'rt', encoding='utf-8') as f:
                for line in f:
                    lines_read += 1
                    try:
                        data = json.loads(line)
                        
                        if data.get('$schemaRef') == "https://eddn.edcd.io/schemas/journal/1":
                            conflicts = extract_relevant_conflicts(data, TARGET_FACTIONS)
                            
                            for c in conflicts:
                                upsert_conflict(
                                    conn, c['system'], c['faction_1'], c['faction_2'],
                                    c['war_type'], c['status'], c['f1_days'], 
                                    c['f2_days'], c['stake1'], c['stake2'], 
                                    c['timestamp'], "DUMP"
                                    )
                                conflicts_inserted += 1
                    except (json.JSONDecodeError, KeyError):
                        continue
                        
                    if lines_read % 100000 == 0:
                        print(f" -> {lines_read} rows have been read from {elem.name}...")

        if elem.is_file() and 'Journal' in elem.name and elem.name.endswith('.jsonl'):
            # Elaborate .jsonl files
            print(f"Elaborating file: {elem.name}...")
            lines_read = 0

            with open(elem.path, 'r', encoding='utf-8') as f:
                for line in f:
                    lines_read += 1
                    try:
                        data = json.loads(line)

                        if data.get('$schemaRef') == "https://eddn.edcd.io/schemas/journal/1":
                            conflicts = extract_relevant_conflicts(data, TARGET_FACTIONS)

                            for c in conflicts:
                                upsert_conflict(
                                    conn, c['system'], c['faction_1'], c['faction_2'],
                                    c['war_type'], c['status'], c['f1_days'],
                                    c['f2_days'], c['stake1'], c['stake2'],
                                    c['timestamp'], "DUMP"
                                    )
                                conflicts_inserted += 1
                    except (json.JSONDecodeError, KeyError):
                        continue

                    if lines_read % 100000 == 0:
                        print(f" -> {lines_read} rows have been read from {elem.name}...")
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



def process_dump():
    valid_bz2_files = download_latest_dumps()
    valid_jsonl_files = download_latest_dump()
    
    if valid_bz2_files is None and valid_jsonl_files is None:
        print("Downloading failed. Import aborted.")
        return

    # Substitute None with empty lists to avoid errors
    valid_bz2_files = valid_bz2_files or []
    valid_jsonl_files = valid_jsonl_files or []

    all_valid_files = valid_bz2_files + valid_jsonl_files
    clean_old_dumps(all_valid_files)    

    conn = get_connection(DB_PATH)
    setup_db(conn)
    elaborate_data(conn)
    conn.close()

if __name__ == "__main__":
    process_dump()
