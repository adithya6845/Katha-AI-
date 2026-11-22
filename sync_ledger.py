import sqlite3
import openpyxl
import os
import time

DB_PATH = os.path.join(os.path.dirname(__file__), "sme_data.db")
ROOT_XLSX = os.path.join(os.path.dirname(__file__), "ledger.xlsx")
DATA_XLSX = os.path.join(os.path.dirname(__file__), "data", "ledger.xlsx")

def sync_root_ledger():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, created_at, customer_name, type, amount, notes FROM ledger_entries ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("No rows in database to sync.")
        return False

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ledger"
    ws.append(["ID", "Date/Time", "Customer Name", "Type", "Amount (INR)", "Notes"])

    for r in rows:
        ws.append([r[0], str(r[1] or ""), str(r[2] or ""), str(r[3] or ""), float(r[4] or 0), str(r[5] or "")])

    # Try saving to root ledger.xlsx
    saved = False
    for attempt in range(5):
        try:
            wb.save(ROOT_XLSX)
            print(f"Successfully synced {len(rows)} entries into {ROOT_XLSX}!")
            saved = True
            break
        except PermissionError:
            print(f"Attempt {attempt+1}: ledger.xlsx is locked by Microsoft Excel. Please close Excel...")
            time.sleep(1)
        except Exception as e:
            print(f"Error saving: {e}")
            break

    # Also make sure a standalone readable copy is always ready
    try:
        wb.save(os.path.join(os.path.dirname(__file__), "ledger_latest.xlsx"))
        print("Saved backup to ledger_latest.xlsx")
    except Exception:
        pass

    return saved

if __name__ == "__main__":
    sync_root_ledger()
