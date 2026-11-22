import time, os, shutil, openpyxl, sqlite3

DB_PATH = "sme_data.db"
ROOT_LEDGER = "ledger.xlsx"
DATA_LEDGER = os.path.join("data", "ledger.xlsx")
LATEST_LEDGER = "ledger_latest.xlsx"

ROOT_COLLECTIONS = "collections.xlsx"
DATA_COLLECTIONS = os.path.join("data", "collections.xlsx")

def is_file_locked(filepath):
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, "r+b"):
            return False
    except IOError:
        return True

def sync_ledger():
    if is_file_locked(ROOT_LEDGER):
        return False

    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id, created_at, customer_name, type, amount, notes FROM ledger_entries ORDER BY id ASC")
        rows = cur.fetchall()
        conn.close()

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Ledger"
        ws.append(["ID", "Date/Time", "Customer Name", "Type", "Amount (INR)", "Notes"])
        for r in rows:
            ws.append([r[0], r[1], r[2], r[3], r[4], r[5] or ""])

        wb.save(ROOT_LEDGER)
        wb.save(LATEST_LEDGER)
        print(f"[Auto-Sync] Synchronized {len(rows)} entries to {ROOT_LEDGER}!")
        return True
    except Exception as e:
        return False

def sync_collections():
    if is_file_locked(ROOT_COLLECTIONS):
        return False

    if os.path.exists(DATA_COLLECTIONS):
        try:
            shutil.copy2(DATA_COLLECTIONS, ROOT_COLLECTIONS)
            print(f"[Auto-Sync] Synchronized {DATA_COLLECTIONS} to {ROOT_COLLECTIONS}!")
            return True
        except Exception:
            return False
    return False

print("Auto-sync watcher running. Monitoring Excel file locks...")
while True:
    try:
        sync_ledger()
        sync_collections()
        time.sleep(2)
    except Exception as e:
        time.sleep(2)

# Updated: 2026-11-22
