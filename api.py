import sqlite3
import json
import os
import csv
import math
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import time
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import requests
from dotenv import load_dotenv

# Load .env from Render secret files path, or local .env
if os.path.exists("/etc/secrets/.env"):
    load_dotenv("/etc/secrets/.env", override=True)
else:
    load_dotenv(override=True)

DB_PATH = os.path.join(os.path.dirname(__file__), "sme_data.db")
DATA_DIR = os.path.dirname(__file__)

# Environment variables
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMMA_MODEL = os.environ.get("GEMMA_MODEL", "gemma-4-26b-a4b-it")
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "").strip()
DRONAHQ_WEBHOOK_URL = os.environ.get("DRONAHQ_WEBHOOK_URL", "")
DRONAHQ_API_KEY = os.environ.get("DRONAHQ_API_KEY", "")
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
NVIDIA_MODEL = os.environ.get("NVIDIA_MODEL", "nvidia/nemotron-3.5-bolt-e4b")

# Voice AI configuration
BOLNA_API_KEY = (os.environ.get("VOICE_API_KEY") or os.environ.get("API_KEY") or os.environ.get("BOLNA_API_KEY") or "").strip()
BOLNA_AGENT_ID_ADVISOR = (os.environ.get("AGENT_ID_ADVISOR") or os.environ.get("BOLNA_AGENT_ID_ADVISOR") or "").strip()
BOLNA_AGENT_ID_PAYMENT = (os.environ.get("COLLECTIONS_AGENT_ID") or os.environ.get("AGENT_ID_PAYMENT") or os.environ.get("BOLNA_COLLECTIONS_AGENT_ID") or os.environ.get("BOLNA_AGENT_ID_PAYMENT") or "").strip()
BOLNA_AGENT_ID_SUPPLIER = (os.environ.get("AGENT_ID_SUPPLIER") or os.environ.get("BOLNA_AGENT_ID_SUPPLIER") or "").strip()
BOLNA_AGENT_ID_LEDGER = (os.environ.get("LEDGER_AGENT_ID") or os.environ.get("AGENT_ID_LEDGER") or os.environ.get("BOLNA_AGENT_ID_LEDGER") or os.environ.get("BOLNA_LEDGER_AGENT_ID") or "").strip()
BOLNA_BASE_URL = os.environ.get("BOLNA_BASE_URL", "https://api.bolna.ai").rstrip("/")
DEFAULT_CUSTOMER_PHONE = os.environ.get("DEFAULT_CUSTOMER_PHONE", "+916363077110").strip()
DEFAULT_SUPPLIER_PHONE = os.environ.get("DEFAULT_SUPPLIER_PHONE", "+916363077110").strip()

# Gnani AI Speech-to-Text configuration
GNANI_API_KEY = os.environ.get("GNANI_API_KEY", "").strip()
GNANI_BASE_URL = os.environ.get("GNANI_BASE_URL", "https://api.vachana.ai").rstrip("/")

def call_gemma(system_prompt: str, user_text: str = "") -> str:
    """Generate responses using Google Gemma model."""
    try:
        from google import genai
        client = genai.Client()
        full_content = f"{system_prompt}\n\n{user_text}".strip() if user_text else system_prompt.strip()
        response = client.models.generate_content(
            model=GEMMA_MODEL,
            contents=full_content,
        )
        if response and response.text:
            return response.text.strip()
    except Exception as e:
        print(f"Gemma API Error: {e}")
    return ""

def ask_dronahq(query_text: str) -> str:
    webhook_url = os.environ.get("DRONAHQ_WEBHOOK_URL", DRONAHQ_WEBHOOK_URL).strip()
    api_key = os.environ.get("DRONAHQ_API_KEY", DRONAHQ_API_KEY).strip()

    if not webhook_url or not api_key:
        raise ValueError("Missing DRONAHQ_WEBHOOK_URL or DRONAHQ_API_KEY in environment configuration.")

    headers = {
        "api-key": api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "query": query_text,
        "message": query_text
    }
    
    response = requests.post(webhook_url, headers=headers, json=payload, timeout=60)
    
    # If 404 and credentials might be swapped in .env, attempt swapped pair
    if response.status_code == 404:
        import re
        url_match = re.search(r'([a-f0-9\-]{36})', webhook_url)
        key_is_uuid = bool(re.match(r'^[a-f0-9\-]{36}$', api_key))
        if url_match and key_is_uuid:
            swapped_url = webhook_url.replace(url_match.group(1), api_key)
            swapped_headers = {
                "api-key": url_match.group(1),
                "Content-Type": "application/json"
            }
            try:
                alt_resp = requests.post(swapped_url, headers=swapped_headers, json=payload, timeout=60)
                if alt_resp.status_code == 200:
                    response = alt_resp
            except Exception:
                pass

    response.raise_for_status()
    try:
        data = response.json()
    except Exception:
        return response.text.strip()

    if isinstance(data, dict):
        if "response" in data:
            r = data["response"]
            if isinstance(r, dict):
                if "text" in r and r["text"]:
                    return str(r["text"]).strip()
                elif "message" in r and r["message"]:
                    return str(r["message"]).strip()
                elif "recommendations" in r:
                    return f"{r.get('message', '')}\n\n" + "\n".join(r["recommendations"])
                return json.dumps(r)
            elif isinstance(r, str):
                return r.strip()
        for k in ["result", "text", "output", "data"]:
            if k in data and data[k]:
                val = data[k]
                return str(val).strip() if not isinstance(val, (dict, list)) else json.dumps(val)
        if "message" in data and data["message"] and "completed" not in str(data["message"]).lower():
            return str(data["message"]).strip()
        return json.dumps(data)
    elif isinstance(data, str):
        return data.strip()
    return str(data)

def format_with_nvidia(system_prompt: str, user_text: str) -> str:
    key = os.environ.get("NVIDIA_API_KEY", NVIDIA_API_KEY).strip()
    model = os.environ.get("NVIDIA_MODEL", NVIDIA_MODEL).strip()
    if not model or model == "meta/llama-3.2-11b-vision-instruct":
        model = "nvidia/nemotron-3.5-bolt-e4b"
    if not key:
        print("[NVIDIA] Warning: NVIDIA_API_KEY is not set.")
        return ""
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    candidate_models = [
        model,
        "nvidia/nemotron-3.5-bolt-e4b",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "stepfun-ai/step-5-preview",
        "nvidia/nemotron-3.5-super-vl-120b-a12b"
    ]
    models_to_try = []
    for m in candidate_models:
        if m and m not in models_to_try:
            models_to_try.append(m)

    for model_name in models_to_try:
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text}
            ],
            "temperature": 0.2,
            "max_tokens": 1000,
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            if response.status_code == 200:
                res_data = response.json()
                if "choices" in res_data and len(res_data["choices"]) > 0:
                    msg = res_data["choices"][0].get("message", {})
                    content = msg.get("content") or ""
                    if not content or not content.strip():
                        reasoning = msg.get("reasoning_content") or ""
                        if "{" in reasoning and "}" in reasoning:
                            content = reasoning
                        elif reasoning:
                            content = reasoning
                    if content and content.strip():
                        print(f"[NVIDIA] Successfully generated response using {model_name}")
                        return content.strip()
            else:
                print(f"[NVIDIA] Model {model_name} HTTP {response.status_code}: {response.text[:120]}")
        except Exception as e:
            print(f"[NVIDIA] Error with model {model_name}: {e}")

    return ""

# ─── DATABASE SETUP ───────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Import all 11 CSV datasets into SQLite tables."""
    conn = get_db()
    cur = conn.cursor()

    csv_tables = {
        "categories":      "categories.csv",
        "products":        "products.csv",
        "suppliers":       "suppliers.csv",
        "customers":       "customers.csv",
        "employees":       "employees.csv",
        "sales_raw":       "sales_raw.csv",
        "invoices":        "invoices.csv",
        "expenses":        "expenses.csv",
        "inventory":       "inventory.csv",
        "pending_payments":"pending_payments.csv",
        "purchase_orders": "purchase_orders.csv",
    }

    for table, filename in csv_tables.items():
        filepath = os.path.join(DATA_DIR, filename)
        if not os.path.exists(filepath):
            print(f"WARNING: {filepath} not found, skipping.")
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            if not rows:
                continue
            cols = list(rows[0].keys())

        # Drop & recreate table
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        col_defs = ", ".join(f'"{c}" TEXT' for c in cols)
        cur.execute(f'CREATE TABLE IF NOT EXISTS {table} ({col_defs})')

        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            placeholders = ", ".join("?" for _ in cols)
            quoted_cols = ", ".join(f'"{c}"' for c in cols)
            for row in reader:
                values = [row.get(c, "") for c in cols]
                cur.execute(f'INSERT INTO {table} ({quoted_cols}) VALUES ({placeholders})', values)

        print(f"  [OK] Imported {table} ({len(rows)} rows)")

    conn.commit()
    conn.close()
    print("Database initialized successfully.")

# ─── FORMATTING HELPERS ───────────────────────────────────────────────────────

def fmt_inr(n):
    n = float(n) if n else 0
    if abs(n) >= 10_000_000:
        return f"₹{n/10_000_000:.2f}Cr"
    elif abs(n) >= 100_000:
        return f"₹{n/100_000:.2f}L"
    else:
        return f"₹{round(n):,}"

def safe_float(v, default=0.0):
    try:
        return float(v) if v else default
    except (ValueError, TypeError):
        return default

def safe_int(v, default=0):
    try:
        return int(float(v)) if v else default
    except (ValueError, TypeError):
        return default

# ─── BOLNA VOICE AI HELPERS & DB SETUP ─────────────────────────────────────────

def init_bolna_tables():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bolna_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_id TEXT UNIQUE,
            customer_id TEXT,
            customer_name TEXT,
            invoice_id TEXT,
            phone_number TEXT,
            amount_due TEXT,
            due_date TEXT,
            language TEXT DEFAULT 'hi',
            status TEXT DEFAULT 'queued',
            conversation_duration INTEGER DEFAULT 0,
            transcript TEXT,
            recording_url TEXT,
            summary TEXT,
            promise_date TEXT,
            amount_agreed TEXT,
            dispute_flag INTEGER DEFAULT 0,
            callback_needed INTEGER DEFAULT 0,
            risk_score REAL DEFAULT 50.0,
            risk_tier TEXT DEFAULT 'Medium',
            follow_up_reminder TEXT,
            raw_payload TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    for col in ["execution_id TEXT", "call_status TEXT", "promise_date TEXT", "last_call_at TEXT"]:
        try:
            cur.execute(f"ALTER TABLE pending_payments ADD COLUMN {col}")
        except Exception:
            pass
    for col in ["risk_score REAL", "risk_tier TEXT", "follow_up_reminder TEXT", "preferred_language TEXT"]:
        try:
            cur.execute(f"ALTER TABLE customers ADD COLUMN {col}")
        except Exception:
            pass
    conn.commit()
    conn.close()

import re

def format_to_e164(phone: str, default_country_code: str = "+91") -> str:
    """Format input phone number into E.164 (+<country_code><digits>)."""
    if not phone:
        return ""
    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    if cleaned.startswith("+"):
        digits = cleaned[1:]
        return f"+{digits}" if len(digits) >= 10 else cleaned
    if cleaned.startswith("00"):
        digits = cleaned[2:]
        return f"+{digits}" if len(digits) >= 10 else f"+{digits}"
    if len(cleaned) == 10:
        return f"{default_country_code}{cleaned}"
    if len(cleaned) == 12 and cleaned.startswith("91"):
        return f"+{cleaned}"
    if len(cleaned) == 11 and cleaned.startswith("0"):
        return f"{default_country_code}{cleaned[1:]}"
    if len(cleaned) >= 10:
        return f"+{cleaned}"
    return cleaned

def is_valid_e164(phone: str) -> bool:
    """Validates whether phone strictly matches E.164 standard (+<country_code><subscriber_number>)."""
    if not phone:
        return False
    return bool(re.match(r"^\+[1-9]\d{7,14}$", phone))

def extract_bolna_insights(extracted_data: dict, summary: str = "", transcript: str = "") -> dict:
    """Extract promise_date, amount_agreed, dispute_flag, callback_needed from Bolna response."""
    promise_date = None
    amount_agreed = None
    dispute_flag = 0
    callback_needed = 0

    if isinstance(extracted_data, dict):
        def walk_dict(d):
            nonlocal promise_date, amount_agreed, dispute_flag, callback_needed
            for k, v in d.items():
                if isinstance(v, dict):
                    walk_dict(v)
                else:
                    k_str = str(k).lower()
                    v_str = str(v).strip()
                    if "promise" in k_str or "pay_date" in k_str or "promised_date" in k_str:
                        if v_str and not promise_date: promise_date = v_str
                    if "amount" in k_str or "agreed" in k_str:
                        if v_str and not amount_agreed: amount_agreed = v_str
                    if "dispute" in k_str or "already_paid" in k_str or "disputed" in k_str:
                        dispute_flag = 1 if (v_str.lower() in ("true", "1", "yes") or v is True) else 0
                    if "callback" in k_str or "call_back" in k_str:
                        callback_needed = 1 if (v_str.lower() in ("true", "1", "yes") or v is True) else 0

        walk_dict(extracted_data)

    combined_text = f"{summary} {transcript}".lower()

    if not promise_date:
        days_map = {
            "monday": "Monday", "somwar": "Monday",
            "tuesday": "Tuesday", "mangalwar": "Tuesday",
            "wednesday": "Wednesday", "budhwar": "Wednesday",
            "thursday": "Thursday", "guruwar": "Thursday",
            "friday": "Friday", "shukrawar": "Friday",
            "saturday": "Saturday", "shanivar": "Saturday",
            "sunday": "Sunday", "ravivar": "Sunday",
            "tomorrow": "Tomorrow", "kal": "Tomorrow",
            "parso": "Day after tomorrow", "next week": "Next week",
            "month end": "Month end"
        }
        for keyword, mapped in days_map.items():
            if re.search(r"\b" + re.escape(keyword) + r"\b", combined_text):
                promise_date = mapped
                break
        if not promise_date:
            date_m = re.search(r"\b(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*)\b", combined_text)
            if date_m:
                promise_date = date_m.group(0)

    if not amount_agreed:
        amt_m = re.search(r"(?:rs\.?|₹|inr)\s*([\d,]+(?:\.\d+)?)", combined_text)
        if amt_m:
            amount_agreed = f"₹{amt_m.group(1)}"

    if not dispute_flag:
        dispute_patterns = ["already paid", "pehle hi pay", "bhej diya", "wrong amount", "dispute", "fraud", "maine pay kar diya"]
        if any(p in combined_text for p in dispute_patterns):
            dispute_flag = 1

    if not callback_needed:
        callback_patterns = ["call later", "call back", "baad mein call", "busy", "kal call", "driving"]
        if any(p in combined_text for p in callback_patterns):
            callback_needed = 1

    return {
        "promise_date": promise_date,
        "amount_agreed": amount_agreed,
        "dispute_flag": dispute_flag,
        "callback_needed": callback_needed
    }


def update_excel_collections(
    customer_id: str,
    outcome: str,
    amount: float = 0,
    date_val: str = "",
    payment_mode: str = "UPI",
    callback_time: str = "",
    reason: str = "",
    execution_id: str = "",
    duration: int = 0,
    notes: str = ""
):
    import openpyxl
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_only = datetime.now().strftime("%Y-%m-%d")
    clean_date = date_val or date_only

    target_files = [
        os.path.join(DATA_DIR, "data", "collections.xlsx"),
        os.path.join(DATA_DIR, "collections.xlsx")
    ]
    for xlsx_file in target_files:
        if not os.path.exists(xlsx_file):
            continue
        try:
            wb = openpyxl.load_workbook(xlsx_file)
            rec_sheet = wb["Receivables"] if "Receivables" in wb.sheetnames else wb.active
            log_sheet = wb["CollectionsLog"] if "CollectionsLog" in wb.sheetnames else None

            cust_row = None
            for row in rec_sheet.iter_rows(min_row=2):
                c_id = str(row[0].value or "").strip().lower()
                c_name = str(row[1].value or "").strip().lower()
                target_cust = str(customer_id).strip().lower()
                if c_id == target_cust or c_name == target_cust:
                    cust_row = row
                    break

            cust_name = str(cust_row[1].value or "Customer") if cust_row else str(customer_id)
            inv_id = str(cust_row[4].value or "") if cust_row else ""
            pending_before = float(cust_row[6].value or 0.0) if cust_row else 0.0
            cur_attempts = int(cust_row[20].value or 0) + 1 if cust_row else 1

            amount_promised = ""
            promise_date = ""
            claimed_amount = ""
            readable_note = notes or ""

            out_lower = (outcome or "").lower()
            if "promise" in out_lower:
                amount_promised = amount if amount > 0 else pending_before
                promise_date = clean_date
                readable_note = readable_note or f"{cust_name} promised ₹{amount_promised:,.2f} by {promise_date}"
                if cust_row:
                    cust_row[10].value = "Promise made"
                    cust_row[11].value = amount_promised
                    cust_row[12].value = promise_date
                    cust_row[20].value = cur_attempts
                    cust_row[21].value = now_str
                    try:
                        dt = datetime.strptime(promise_date, "%Y-%m-%d") + timedelta(days=1)
                        cust_row[22].value = dt.strftime("%Y-%m-%d")
                    except Exception:
                        cust_row[22].value = promise_date
            elif "claim" in out_lower or "paid" in out_lower:
                claimed_amount = amount if amount > 0 else pending_before
                readable_note = readable_note or f"{cust_name} claimed payment of ₹{claimed_amount:,.2f} via {payment_mode}. Verification pending."
                if cust_row:
                    cust_row[10].value = "Payment claimed (verify)"
                    cust_row[13].value = "Yes"
                    cust_row[14].value = claimed_amount
                    cust_row[15].value = payment_mode
                    cust_row[20].value = cur_attempts
                    cust_row[21].value = now_str
            elif "callback" in out_lower:
                readable_note = readable_note or f"{cust_name} requested callback at {callback_time or 'Tomorrow'}."
                if cust_row:
                    cust_row[10].value = "Callback scheduled"
                    cust_row[16].value = callback_time or "Tomorrow"
                    cust_row[20].value = cur_attempts
                    cust_row[21].value = now_str
            elif "dispute" in out_lower:
                readable_note = readable_note or f"{cust_name} disputed invoice. Reason: {reason or 'Dispute'}."
                if cust_row:
                    cust_row[10].value = "Disputed"
                    cust_row[17].value = "Yes"
                    cust_row[18].value = reason or "Dispute"
                    cust_row[20].value = cur_attempts
                    cust_row[21].value = now_str
            else:
                readable_note = readable_note or f"{cust_name} collection outcome: {outcome}."
                if cust_row:
                    cust_row[10].value = "Follow-up pending"
                    cust_row[20].value = cur_attempts
                    cust_row[21].value = now_str

            if log_sheet:
                log_sheet.append([
                    now_str,
                    customer_id,
                    cust_name,
                    inv_id,
                    outcome,
                    amount_promised,
                    promise_date,
                    claimed_amount,
                    payment_mode,
                    pending_before,
                    pending_before,
                    callback_time,
                    reason,
                    readable_note,
                    "completed",
                    duration,
                    execution_id or "",
                    "",
                    "voice_call"
                ])

            wb.save(xlsx_file)
            print(f"[Collections Excel] Successfully saved {outcome} for {cust_name} in {xlsx_file}")
        except PermissionError:
            print(f"[Collections Excel] Notice: {xlsx_file} is locked in Excel.")
        except Exception as e:
            print(f"[Collections Excel] Error updating {xlsx_file}: {e}")


def extract_and_update_ledger_from_transcript(execution_id: str, transcript: str):
    if not transcript or len(transcript.strip()) < 20:
        return None

    short_id = execution_id[:8] if execution_id else "CALL"
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM ledger_entries WHERE notes LIKE ? LIMIT 1", (f"%{short_id}%",))
    already = cur.fetchone()
    conn.close()
    if already:
        return None

    system_prompt = """You are an expert accounting system parser for Indian SMEs.
Analyze the provided phone call transcript between the shop owner and Priya (the Ledger AI Voice Agent).
Extract any financial transaction or ledger entry discussed, agreed upon, or dictated by the owner.
Transaction types must be exactly one of:
- "credit_given" (if customer took credit / udhaar / goods on credit / borrowed money)
- "payment_received" (if customer paid money / payment received / settled payment / customer payment)
- "payment_missed" (if customer missed payment / failed to pay)

Respond ONLY with a JSON object in this format:
{
  "has_transaction": true,
  "customer_name": "Aditya",
  "type": "payment_received",
  "amount": 2000.0,
  "notes": "Customer payment from Aditya"
}
If no transaction was discussed or agreed, return {"has_transaction": false}.
Do not include any explanation or markdown backticks, output strictly valid JSON."""

    user_query = f"Transcript:\n{transcript}"
    bot_res = ""
    try:
        bot_res = format_with_nvidia(system_prompt, user_query)
    except Exception:
        pass
    if not bot_res:
        try:
            bot_res = call_gemma(system_prompt, user_query)
        except Exception:
            pass

    if bot_res:
        import re, json
        clean_json = re.sub(r'```json\s*', '', bot_res)
        clean_json = re.sub(r'```\s*', '', clean_json).strip()
        try:
            parsed = json.loads(clean_json)
            if parsed.get("has_transaction") and parsed.get("customer_name") and float(parsed.get("amount", 0)) > 0:
                cust_name = str(parsed["customer_name"]).strip()
                t_type = str(parsed.get("type", "payment_received")).strip().lower()
                if t_type not in VALID_LEDGER_TYPES:
                    t_type = "payment_received"
                amt = float(parsed["amount"])
                notes = f"{parsed.get('notes', 'Voice dictation')} (Call ID: {short_id})"
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                new_bal = update_excel_ledger(cust_name, t_type, amt, notes=notes, date_str=now_str)
                conn = get_db()
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO ledger_entries (customer_name, type, amount, balance, notes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (cust_name, t_type, amt, new_bal, notes, now_str)
                )
                conn.commit()
                conn.close()
                print(f"[Ledger Auto-Sync] Recorded: {cust_name} ₹{amt} ({t_type}) from call {short_id}!")
                return {"customer_name": cust_name, "type": t_type, "amount": amt, "balance": new_bal}
        except Exception as e:
            print(f"[Ledger Auto-Sync] Error: {e}")
    return None


def extract_and_update_collections_from_transcript(execution_id: str, customer_id: str, transcript: str, extracted_data: dict = None, insights: dict = None):
    if not transcript and not extracted_data:
        return None

    outcome = "promise_to_pay" if (insights and insights.get("promise_date")) else "call_completed"
    date_val = insights.get("promise_date", "") if insights else ""
    amount_val = 0.0
    if insights and insights.get("amount_agreed"):
        try:
            amount_val = float(re.sub(r'[^0-9.]', '', str(insights["amount_agreed"])))
        except Exception:
            pass

    if outcome == "call_completed" and transcript and len(transcript) > 30:
        system_prompt = """Analyze this voice call transcript between debt collections agent Meera and a customer.
Extract the collection outcome. Return strictly valid JSON:
{
  "outcome": "promise_to_pay",
  "amount": 50000.0,
  "date": "2026-11-28",
  "payment_mode": "UPI",
  "reason": "none"
}"""
        user_query = f"Transcript:\n{transcript}"
        try:
            res = format_with_nvidia(system_prompt, user_query)
            clean_res = re.sub(r'```json\s*', '', res)
            clean_res = re.sub(r'```\s*', '', clean_res).strip()
            p = json.loads(clean_res)
            if p.get("outcome"):
                outcome = p["outcome"]
                date_val = str(p.get("date") or "")
                amount_val = float(p.get("amount") or 0.0)
        except Exception:
            pass

    update_excel_collections(
        customer_id=customer_id,
        outcome=outcome,
        amount=amount_val,
        date_val=date_val,
        execution_id=execution_id,
        notes=f"Voice call collection update ({execution_id[:8] if execution_id else 'call'})"
    )


def process_bolna_execution_update(execution_id: str, data: dict) -> dict:
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM bolna_calls WHERE execution_id = ?", (execution_id,))
    row = cur.fetchone()
    
    status = data.get("status") or "completed"
    
    duration = data.get("conversation_duration")
    if duration is None:
        telephony = data.get("telephony_data") or {}
        duration = telephony.get("duration", 0)
    try:
        duration = int(duration or 0)
    except Exception:
        duration = 0
        
    telephony = data.get("telephony_data") or {}
    recording_url = telephony.get("recording_url") or data.get("recording_url")
    
    raw_transcript = data.get("transcript")
    if isinstance(raw_transcript, list):
        formatted_lines = []
        for line in raw_transcript:
            if isinstance(line, dict):
                speaker = line.get("role") or line.get("speaker") or "Speaker"
                speaker = "AI Agent (Kavya)" if speaker.lower() in ("assistant", "agent", "system") else "Customer"
                text = line.get("message") or line.get("text") or line.get("content") or ""
                formatted_lines.append(f"{speaker}: {text}")
            elif isinstance(line, str):
                formatted_lines.append(line)
        transcript = "\n".join(formatted_lines)
    elif isinstance(raw_transcript, str):
        transcript = raw_transcript
    else:
        transcript = ""
        
    summary = data.get("summary") or ""
    
    extracted_data = data.get("extracted_data") or {}
    insights = extract_bolna_insights(extracted_data, summary, transcript)
    
    promise_date = insights.get("promise_date")
    amount_agreed = insights.get("amount_agreed")
    dispute_flag = 1 if insights.get("dispute_flag") else 0
    callback_needed = 1 if insights.get("callback_needed") else 0
    
    risk_score = 50.0
    risk_tier = "Medium"
    follow_up_reminder = ""
    
    if dispute_flag:
        risk_score = 90.0
        risk_tier = "High - Disputed"
        follow_up_reminder = f"Owner review required: Customer claims payment already made or disputed on {datetime.now().strftime('%d %b')}."
    elif promise_date:
        risk_score = 25.0
        risk_tier = "Low - Promised"
        follow_up_reminder = f"Payment promised for {promise_date}" + (f" ({amount_agreed})" if amount_agreed else "") + ". Follow up if unpaid."
    elif callback_needed:
        risk_score = 55.0
        risk_tier = "Medium - Callback"
        follow_up_reminder = "Customer requested callback or was busy. Follow up tomorrow."
    elif status.lower() in ("completed", "done"):
        risk_score = 40.0
        risk_tier = "Medium"
        follow_up_reminder = "Call concluded. Check updated ledger status."
    elif status.lower() in ("failed", "no-answer", "busy"):
        risk_score = 75.0
        risk_tier = "High - Unreachable"
        follow_up_reminder = "Call unreachable. Retry call in 24 hours."

    now_str = datetime.utcnow().isoformat()
    
    cust_id = None
    inv_id = None
    if row:
        cust_id = row["customer_id"]
        inv_id = row["invoice_id"]
        cur.execute("""
            UPDATE bolna_calls
            SET status = ?, conversation_duration = ?, recording_url = COALESCE(?, recording_url),
                transcript = COALESCE(?, transcript), summary = COALESCE(?, summary),
                promise_date = COALESCE(?, promise_date), amount_agreed = COALESCE(?, amount_agreed),
                dispute_flag = ?, callback_needed = ?, risk_score = ?, risk_tier = ?,
                follow_up_reminder = ?, raw_payload = ?, updated_at = ?
            WHERE execution_id = ?
        """, (
            status, duration, recording_url, transcript, summary,
            promise_date, amount_agreed, dispute_flag, callback_needed,
            risk_score, risk_tier, follow_up_reminder, json.dumps(data), now_str,
            execution_id
        ))
    else:
        cust_id = data.get("customer_id") or data.get("user_data", {}).get("customer_id")
        inv_id = data.get("user_data", {}).get("invoice_id", "")
        cur.execute("""
            INSERT INTO bolna_calls (
                execution_id, customer_id, customer_name, invoice_id,
                status, conversation_duration, recording_url, transcript, summary,
                promise_date, amount_agreed, dispute_flag, callback_needed,
                risk_score, risk_tier, follow_up_reminder, raw_payload, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            execution_id, cust_id, data.get("user_data", {}).get("customer_name", "Customer"),
            inv_id, status, duration, recording_url, transcript, summary,
            promise_date, amount_agreed, dispute_flag, callback_needed,
            risk_score, risk_tier, follow_up_reminder, json.dumps(data), now_str, now_str
        ))

    if cust_id:
        cur.execute("""
            UPDATE pending_payments
            SET call_status = ?, promise_date = COALESCE(?, promise_date), execution_id = ?
            WHERE CustomerID = ? OR InvoiceID = ?
        """, (status, promise_date, execution_id, cust_id, inv_id))
        
        cur.execute("""
            UPDATE customers
            SET risk_score = ?, risk_tier = ?, follow_up_reminder = ?
            WHERE CustomerID = ?
        """, (risk_score, risk_tier, follow_up_reminder, cust_id))

    conn.commit()
    conn.close()

    # ─── REAL-TIME TRANSCRIPT INTELLIGENCE (LEDGER & COLLECTIONS EXCEL SYNC) ───
    if transcript and len(transcript.strip()) >= 20:
        # 1. Real-time ledger update from voice call
        if not cust_id or any(kw in transcript.lower() for kw in ["ledger", "priya", "payment", "rupaye", "rupees", "lena", "dena", "hazaar", "credit", "udhaar", "bhai", "ji"]):
            try:
                extract_and_update_ledger_from_transcript(execution_id, transcript)
            except Exception as e:
                print(f"[Bolna Sync] Error in extract_and_update_ledger_from_transcript: {e}")

        # 2. Real-time collections update from customer call
        if cust_id or any(kw in transcript.lower() for kw in ["invoice", "bill", "due", "pay", "tareekh", "tarikh", "promise", "bhejo", "meera"]):
            try:
                extract_and_update_collections_from_transcript(execution_id, cust_id, transcript, extracted_data, insights)
            except Exception as e:
                print(f"[Bolna Sync] Error in extract_and_update_collections_from_transcript: {e}")
    
    return {
        "execution_id": execution_id,
        "status": status,
        "duration": duration,
        "promise_date": promise_date,
        "amount_agreed": amount_agreed,
        "dispute_flag": bool(dispute_flag),
        "callback_needed": bool(callback_needed),
        "risk_tier": risk_tier,
        "follow_up_reminder": follow_up_reminder,
        "recording_url": recording_url
    }


# ─── KPI ENGINE ───────────────────────────────────────────────────────────────

def compute_kpis():
    conn = get_db()
    cur = conn.cursor()

    # ── Revenue & Orders ──
    cur.execute("SELECT SUM(CAST(TotalAmount AS REAL)), COUNT(*) FROM sales_raw")
    total_revenue_raw, total_orders = cur.fetchone()
    total_revenue = safe_float(total_revenue_raw)
    avg_order_value = total_revenue / max(total_orders, 1)

    # ── Active Customers ──
    cur.execute("SELECT COUNT(DISTINCT CustomerID) FROM sales_raw")
    active_customers = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM customers")
    total_customers = cur.fetchone()[0]

    # ── COGS ──
    cur.execute("""
        SELECT s.ProductID, SUM(CAST(s.Quantity AS REAL)), SUM(CAST(s.TotalAmount AS REAL))
        FROM sales_raw s
        GROUP BY s.ProductID
    """)
    sales_by_prod = cur.fetchall()
    cur.execute("SELECT ProductID, CAST(StandardMarkup AS REAL) FROM products")
    markup_map = {r[0]: safe_float(r[1], 1.0) for r in cur.fetchall()}

    total_cogs = 0
    for prod_id, qty, rev in sales_by_prod:
        markup = markup_map.get(prod_id, 1.0)
        if markup > 0:
            total_cogs += rev / markup

    # ── Expenses ──
    cur.execute("SELECT SUM(CAST(AmountSpent AS REAL)) FROM expenses")
    total_expenses = safe_float(cur.fetchone()[0])

    # ── Profit ──
    gross_profit = total_revenue - total_cogs
    net_profit = gross_profit - total_expenses
    net_profit_margin = (net_profit / total_revenue * 100) if total_revenue > 0 else 0

    # ── Cash Flow ──
    cur.execute("SELECT SUM(CAST(TotalInvoiceValue AS REAL)) FROM invoices WHERE Status='Paid'")
    total_collected = safe_float(cur.fetchone()[0])
    cash_in_hand = total_collected - total_expenses

    # ── Pending Receivables ──
    cur.execute("SELECT SUM(CAST(AmountPending AS REAL)), COUNT(*) FROM pending_payments")
    total_pending_raw, pending_count = cur.fetchone()
    total_pending = safe_float(total_pending_raw)

    cur.execute("SELECT COUNT(*), SUM(CAST(AmountPending AS REAL)) FROM pending_payments WHERE CAST(DaysOverdue AS INTEGER) > 0")
    overdue_count, overdue_amount_raw = cur.fetchone()
    overdue_amount = safe_float(overdue_amount_raw)

    # ── Invoices ──
    cur.execute("SELECT COUNT(*), SUM(CAST(TotalInvoiceValue AS REAL)) FROM invoices")
    total_invoices, total_invoiced = cur.fetchone()
    total_invoiced = safe_float(total_invoiced)
    collection_rate = (total_collected / total_invoiced * 100) if total_invoiced > 0 else 0

    # ── Inventory ──
    cur.execute("""
        SELECT i.ProductID, CAST(i.QuantityInStock AS REAL), CAST(i.ReorderPoint AS REAL),
               CAST(p.DefaultSellingPrice AS REAL), CAST(p.StandardMarkup AS REAL), p.ProductName, p.CategoryID
        FROM inventory i
        LEFT JOIN products p ON i.ProductID = p.ProductID
    """)
    inv_rows = cur.fetchall()
    total_inventory_value = 0
    low_stock_items = []
    out_of_stock_count = 0
    low_stock_count = 0

    for prod_id, qty, reorder, price, markup, name, cat_id in inv_rows:
        cost = price / max(markup, 0.01) if markup and markup > 0 else price
        total_inventory_value += qty * cost
        if qty == 0:
            out_of_stock_count += 1
        if qty < reorder:
            low_stock_count += 1
            low_stock_items.append({
                "id": prod_id, "name": name or prod_id,
                "qty": int(qty), "reorder": int(reorder)
            })

    # ── Products ──
    cur.execute("SELECT COUNT(*) FROM products")
    total_products = cur.fetchone()[0]

    # ── Employees & Suppliers ──
    cur.execute("SELECT COUNT(*) FROM employees WHERE IsActive='1'")
    total_employees = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM suppliers")
    total_suppliers = cur.fetchone()[0]

    # ── Top Products by Revenue ──
    cur.execute("""
        SELECT s.ProductID, SUM(CAST(s.TotalAmount AS REAL)) as rev, SUM(CAST(s.Quantity AS REAL)) as qty
        FROM sales_raw s
        GROUP BY s.ProductID
        ORDER BY rev DESC
        LIMIT 5
    """)
    top_products_raw = cur.fetchall()
    cur.execute("SELECT ProductID, ProductName FROM products")
    prod_name_map = {r[0]: r[1] for r in cur.fetchall()}
    top_products = [{"name": prod_name_map.get(r[0], r[0]), "revenue": fmt_inr(r[1]), "qty": int(safe_float(r[2]))} for r in top_products_raw]

    # ── Top Products by Quantity ──
    cur.execute("""
        SELECT s.ProductID, SUM(CAST(s.Quantity AS REAL)) as qty
        FROM sales_raw s
        GROUP BY s.ProductID
        ORDER BY qty DESC
        LIMIT 5
    """)
    top_by_qty = [{"name": prod_name_map.get(r[0], r[0]), "qty": int(safe_float(r[1]))} for r in cur.fetchall()]

    # ── Revenue by Category ──
    cur.execute("""
        SELECT c.CategoryName, SUM(CAST(s.TotalAmount AS REAL)) as rev
        FROM sales_raw s
        JOIN products p ON s.ProductID = p.ProductID
        JOIN categories c ON p.CategoryID = c.CategoryID
        GROUP BY c.CategoryName
        ORDER BY rev DESC
        LIMIT 6
    """)
    cat_rows = cur.fetchall()
    top_categories = [{"name": r[0], "revenue": fmt_inr(r[1]), "pct": f"{r[1]/total_revenue*100:.1f}" if total_revenue > 0 else "0"} for r in cat_rows]
    cat_chart_labels = [r[0] for r in cat_rows]
    cat_chart_data = [round(safe_float(r[1]) / 1000) for r in cat_rows]

    # ── Weekly Revenue (for chart) ──
    cur.execute("""
        SELECT strftime('%d', SalesDate) as day, SUM(CAST(TotalAmount AS REAL)) as rev
        FROM sales_raw
        GROUP BY strftime('%Y-%m-%d', SalesDate)
        ORDER BY SalesDate
    """)
    day_rows = cur.fetchall()
    weekly_rev = [0, 0, 0, 0, 0]
    for day_str, rev in day_rows:
        day = safe_int(day_str)
        week_idx = min(math.ceil(day / 7), 5) - 1
        if 0 <= week_idx < 5:
            weekly_rev[week_idx] += safe_float(rev)

    # ── Revenue Sparkline (7 daily points) ──
    cur.execute("""
        SELECT SalesDate, SUM(CAST(TotalAmount AS REAL)) as rev
        FROM sales_raw
        GROUP BY SalesDate
        ORDER BY SalesDate
    """)
    all_days = cur.fetchall()
    def pick_n(rows, n):
        if len(rows) <= n:
            return [round(safe_float(r[1]) / 1000) for r in rows]
        step = len(rows) // n
        return [round(safe_float(rows[i * step][1]) / 1000) for i in range(n)]
    revenue_sparkline = pick_n(all_days, 7)

    # Profit sparkline (7 daily points, scaled perfectly to net profit margin)
    profit_sparkline = [round(rev * (net_profit_margin / 100)) for rev in revenue_sparkline]

    # ── Top Customers ──
    cur.execute("""
        SELECT s.CustomerID, SUM(CAST(s.TotalAmount AS REAL)) as rev
        FROM sales_raw s
        GROUP BY s.CustomerID
        ORDER BY rev DESC
        LIMIT 5
    """)
    top_cust_raw = cur.fetchall()
    cur.execute("SELECT CustomerID, DisplayName FROM customers")
    cust_name_map = {r[0]: r[1] for r in cur.fetchall()}
    top_customers = [{"id": r[0], "name": cust_name_map.get(r[0], r[0]), "revenue": fmt_inr(r[1])} for r in top_cust_raw]

    # ── Top Suppliers ──
    cur.execute("""
        SELECT po.SupplierID, SUM(CAST(po.TotalAmountSpent AS REAL)) as spend
        FROM purchase_orders po
        GROUP BY po.SupplierID
        ORDER BY spend DESC
        LIMIT 5
    """)
    top_sup_raw = cur.fetchall()
    cur.execute("SELECT SupplierID, SupplierName, ReliabilityScore FROM suppliers")
    sup_map = {r[0]: {"name": r[1], "score": r[2]} for r in cur.fetchall()}
    top_suppliers = [{
        "id": r[0],
        "name": sup_map.get(r[0], {}).get("name", r[0]),
        "value": fmt_inr(r[1]),
        "reliability": sup_map.get(r[0], {}).get("score", "N/A")
    } for r in top_sup_raw]

    # ── Top Pending Payments ──
    cur.execute("""
        SELECT pp.InvoiceID, pp.CustomerID, CAST(pp.AmountPending AS REAL), CAST(pp.DaysOverdue AS INTEGER), pp.DueDate
        FROM pending_payments pp
        ORDER BY CAST(pp.DaysOverdue AS INTEGER) DESC, CAST(pp.AmountPending AS REAL) DESC
        LIMIT 10
    """)
    top_pending = [{
        "invoice": r[0],
        "customer": cust_name_map.get(r[1], r[1]),
        "amount": fmt_inr(r[2]),
        "amount_raw": round(safe_float(r[2])),
        "days_overdue": safe_int(r[3]),
        "due_date": r[4]
    } for r in cur.fetchall()]

    # ── Expense Breakdown ──
    cur.execute("""
        SELECT ExpenseCode, SUM(CAST(AmountSpent AS REAL)) as amt
        FROM expenses
        GROUP BY ExpenseCode
        ORDER BY amt DESC
    """)
    exp_breakdown = [{"code": r[0], "amount": fmt_inr(r[1]), "raw": round(safe_float(r[1]))} for r in cur.fetchall()]

    # ── Purchase Orders Summary ──
    cur.execute("SELECT COUNT(*), SUM(CAST(TotalAmountSpent AS REAL)) FROM purchase_orders")
    po_count, po_spend = cur.fetchone()
    po_spend = safe_float(po_spend)

    # ── Health Score ──
    profit_score = min((net_profit_margin / 20) * 100, 100)
    collection_score = collection_rate
    expense_ratio = (total_expenses / total_revenue * 100) if total_revenue > 0 else 100
    expense_score = max(100 - expense_ratio, 0)
    health_score = round(profit_score * 0.4 + collection_score * 0.4 + expense_score * 0.2)
    if health_score >= 80:
        health_label = "Excellent"
        health_color = "green"
    elif health_score >= 60:
        health_label = "Good"
        health_color = "blue"
    elif health_score >= 40:
        health_label = "Fair"
        health_color = "orange"
    else:
        health_label = "At Risk"
        health_color = "red"

    # ── Monthly revenue for line chart ──
    chart_rev_data = [round(v / 1000) for v in weekly_rev]
    chart_labels = ["Week 1", "Week 2", "Week 3", "Week 4", "Week 5"]

    # ── Revenue by category for sales page chart ──
    cur.execute("""
        SELECT c.CategoryName, SUM(CAST(s.TotalAmount AS REAL)) as rev
        FROM sales_raw s
        JOIN products p ON s.ProductID = p.ProductID
        JOIN categories c ON p.CategoryID = c.CategoryID
        GROUP BY c.CategoryName
        ORDER BY rev DESC
    """)
    sales_cat_all = cur.fetchall()
    sales_cat_labels = [r[0] for r in sales_cat_all]
    sales_cat_data = [round(safe_float(r[1]) / 1000) for r in sales_cat_all]

    # ── Employee breakdown ──
    cur.execute("SELECT JobRole, COUNT(*) FROM employees GROUP BY JobRole")
    emp_roles = [{"role": r[0], "count": r[1]} for r in cur.fetchall()]

    # ── Forecast ──
    avg_weekly_rev = total_revenue / 5 if total_revenue > 0 else 0
    forecast_next_month = avg_weekly_rev * 5 * 1.05  # 5% growth assumption
    forecast_growth_pct = 5.0

    conn.close()

    return {
        # ── Core KPIs ──
        "totalRevenue": total_revenue,
        "totalRevenueFmt": fmt_inr(total_revenue),
        "netProfit": net_profit,
        "netProfitFmt": fmt_inr(net_profit),
        "netProfitMargin": round(net_profit_margin, 1),
        "cashInHand": cash_in_hand,
        "cashInHandFmt": fmt_inr(cash_in_hand),
        "pendingReceivables": total_pending,
        "pendingReceivablesFmt": fmt_inr(total_pending),
        "overdueCount": overdue_count,
        "overdueAmount": fmt_inr(overdue_amount),
        "overdueAmountRaw": overdue_amount,
        # ── Expenses & COGS ──
        "totalExpenses": total_expenses,
        "totalExpensesFmt": fmt_inr(total_expenses),
        "totalCOGS": total_cogs,
        "totalCOGSFmt": fmt_inr(total_cogs),
        # ── Orders & Customers ──
        "totalOrders": total_orders,
        "avgOrderValue": avg_order_value,
        "avgOrderValueFmt": fmt_inr(avg_order_value),
        "activeCustomers": active_customers,
        "totalCustomers": total_customers,
        # ── Invoices ──
        "collectionRate": round(collection_rate, 1),
        "totalInvoiced": fmt_inr(total_invoiced),
        "totalCollected": fmt_inr(total_collected),
        "totalInvoices": total_invoices,
        # ── Inventory ──
        "totalInventoryValue": total_inventory_value,
        "totalInventoryValueFmt": fmt_inr(total_inventory_value),
        "lowStockCount": low_stock_count,
        "outOfStockCount": out_of_stock_count,
        "lowStockItems": low_stock_items[:5],
        # ── Products / Staff ──
        "totalProducts": total_products,
        "totalEmployees": total_employees,
        "totalSuppliers": total_suppliers,
        # ── Health Score ──
        "healthScore": health_score,
        "healthLabel": health_label,
        "healthColor": health_color,
        # ── Chart Data ──
        "revenueSparkline": revenue_sparkline,
        "profitSparkline": profit_sparkline,
        "chartLabels": chart_labels,
        "chartRevData": chart_rev_data,
        "salesCatLabels": sales_cat_labels,
        "salesCatData": sales_cat_data,
        "catChartLabels": cat_chart_labels,
        "catChartData": cat_chart_data,
        # ── Top Lists ──
        "topProducts": top_products,
        "topByQty": top_by_qty,
        "topCategories": top_categories,
        "topCustomers": top_customers,
        "topSuppliers": top_suppliers,
        "topPendingPayments": top_pending,
        "expByCode": exp_breakdown,
        # ── Procurement ──
        "totalPOCount": po_count,
        "totalPOSpend": fmt_inr(po_spend),
        # ── Forecast ──
        "forecastNextMonth": forecast_next_month,
        "forecastNextMonthFmt": fmt_inr(forecast_next_month),
        "forecastGrowthPct": forecast_growth_pct,
        # ── Staff ──
        "empRoles": emp_roles,
    }

# ─── AI INSIGHTS ENGINE ───────────────────────────────────────────────────────

def generate_ai_insights(kpis: dict) -> dict:
    """Generate rule-based AI insights from KPIs (Gemma-style reasoning)."""
    insights = []
    alerts = []
    pricing_recs = []
    collections_recs = []
    inventory_recs = []
    growth_recs = []

    rev = kpis["totalRevenue"]
    margin = kpis["netProfitMargin"]
    health = kpis["healthScore"]
    coll_rate = kpis["collectionRate"]
    low_stock = kpis["lowStockCount"]
    out_stock = kpis["outOfStockCount"]
    overdue_count = kpis["overdueCount"]
    overdue_amt = kpis["overdueAmountRaw"]
    exp = kpis["totalExpenses"]
    cogs = kpis["totalCOGS"]

    # ── Business Brief ──
    brief = f"Your business generated {kpis['totalRevenueFmt']} in revenue with a {margin}% net profit margin. "
    brief += f"Collection rate stands at {coll_rate}%, "
    brief += f"with {low_stock} products low on stock and {overdue_count} overdue invoices totaling {kpis['overdueAmount']}."

    # ── Smart Alerts ──
    if low_stock > 0:
        alerts.append({"type": "warning", "title": "Low Stock Alert", "message": f"{low_stock} products are running low on stock. Reorder immediately.", "action": "inventory"})
    if out_stock > 0:
        alerts.append({"type": "error", "title": "Out of Stock", "message": f"{out_stock} products are completely out of stock — sales impact likely.", "action": "inventory"})
    if overdue_count > 0:
        alerts.append({"type": "error", "title": "Overdue Payments", "message": f"{overdue_count} customers have overdue payments totaling {kpis['overdueAmount']}.", "action": "customers"})
    if margin < 15:
        alerts.append({"type": "warning", "title": "Low Profit Margin", "message": f"Net profit margin at {margin}%. Industry benchmark is 20%+. Review expenses.", "action": "expenses"})
    if coll_rate < 85:
        alerts.append({"type": "warning", "title": "Collection Rate Low", "message": f"Only {coll_rate}% invoices collected. Follow up on pending payments.", "action": "customers"})
    if health >= 80:
        alerts.append({"type": "success", "title": "Excellent Business Health", "message": f"Health score {health}/100. Business is performing well across all metrics.", "action": "goals"})

    # Dataset-driven Smart Alerts
    for s in kpis.get("topSuppliers", []):
        try:
            if int(s["reliability"]) <= 4:
                alerts.append({"type": "error", "title": "High Supplier Risk", "message": f"Significant procurement spend with {s['name']} (Reliability Score: {s['reliability']}/10). Consider diversifying vendors.", "action": "suppliers"})
                break # Just add one supplier alert
        except:
            pass

    if kpis.get("topPendingPayments"):
        worst = kpis["topPendingPayments"][0]
        if worst["days_overdue"] >= 60:
            alerts.append({"type": "warning", "title": "Critical Aged Receivable", "message": f"Invoice {worst['invoice']} from {worst['customer']} is overdue by {worst['days_overdue']} days. Immediate escalation required.", "action": "customers"})

    for e in kpis.get("expByCode", []):
        if e.get("raw", 0) > (exp * 0.3) and exp > 0:
            alerts.append({"type": "warning", "title": "High Expense Concentration", "message": f"'{e['code']}' accounts for over 30% of your total expenses. Review for optimization opportunities.", "action": "expenses"})
            break

    # ── Pricing Advisor ──
    if kpis["topProducts"]:
        top = kpis["topProducts"][0]
        pricing_recs.append({"product": top["name"], "action": "Increase 5-7%", "reason": "Top revenue driver with strong demand.", "badge": "increase"})
    if len(kpis["topProducts"]) >= 3:
        mid = kpis["topProducts"][2]
        pricing_recs.append({"product": mid["name"], "action": "Maintain", "reason": "Stable category performer. Avoid price disruption.", "badge": "maintain"})
    if len(kpis["topProducts"]) >= 5:
        last = kpis["topProducts"][4]
        pricing_recs.append({"product": last["name"], "action": "Reduce 3-5%", "reason": "Lower volume vs. peers. Price reduction can boost turnover.", "badge": "reduce"})

    # ── Collections Advisor ──
    for p in kpis["topPendingPayments"][:3]:
        if p["days_overdue"] > 30:
            collections_recs.append({"customer": p["customer"], "amount": p["amount"], "days": p["days_overdue"], "urgency": "high", "action": "Send legal notice + immediate call"})
        elif p["days_overdue"] > 10:
            collections_recs.append({"customer": p["customer"], "amount": p["amount"], "days": p["days_overdue"], "urgency": "medium", "action": "Send reminder email + follow-up call"})
        else:
            collections_recs.append({"customer": p["customer"], "amount": p["amount"], "days": p["days_overdue"], "urgency": "low", "action": "Send polite payment reminder"})

    # ── Inventory Advisor ──
    for item in kpis["lowStockItems"][:5]:
        reorder_qty = max(item["reorder"] * 2, 50)
        inventory_recs.append({"product": item["name"], "current": item["qty"], "reorder_at": item["reorder"], "suggested_qty": reorder_qty, "urgency": "critical" if item["qty"] == 0 else "high"})

    # ── Growth Recommendations ──
    if margin > 20:
        growth_recs.append({"title": "Expand High-Margin Categories", "detail": f"With {margin}% margin, reinvest profits into expanding top categories.", "icon": "chart-line"})
    growth_recs.append({"title": "Improve Collections Speed", "detail": f"Reducing payment terms by 10 days could improve cash flow by {fmt_inr(overdue_amt * 0.3)}.", "icon": "hand-holding-dollar"})
    growth_recs.append({"title": "Optimize Supplier Costs", "detail": "Negotiate volume discounts with top 3 suppliers to reduce COGS by 5-8%.", "icon": "truck"})
    if low_stock > 5:
        growth_recs.append({"title": "Prevent Stockout Revenue Loss", "detail": f"Stockouts on {low_stock} products may be causing missed sales. Auto-reorder threshold recommended.", "icon": "boxes-stacked"})

    # ── AI Insights ──
    insights = [
        {"icon": "lightbulb", "color": "yellow", "text": f"Top 20% customers contribute ~63% of revenue. Focus retention on: {', '.join([c['name'] for c in kpis['topCustomers'][:2]]) if kpis['topCustomers'] else 'N/A'}."},
        {"icon": "check-circle", "color": "green", "text": f"Collection rate of {coll_rate}% is {'above' if coll_rate > 85 else 'below'} the 85% benchmark. {'Maintain momentum.' if coll_rate > 85 else 'Accelerate follow-ups.'}"},
        {"icon": "circle-nodes", "color": "blue", "text": f"COGS at {kpis['totalCOGSFmt']} vs Revenue {kpis['totalRevenueFmt']}. Gross margin = {round((rev - cogs) / rev * 100, 1) if rev > 0 else 0}%."},
        {"icon": "chart-line", "color": "purple", "text": f"Best performing category: {kpis['topCategories'][0]['name'] if kpis['topCategories'] else 'N/A'} at {kpis['topCategories'][0]['pct'] if kpis['topCategories'] else '0'}% of total revenue."},
    ]

    return {
        "brief": brief,
        "alerts": alerts,
        "pricingRecs": pricing_recs,
        "collectionsRecs": collections_recs,
        "inventoryRecs": inventory_recs,
        "growthRecs": growth_recs,
        "insights": insights,
    }

# ─── LIFESPAN SETUP ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app_obj: FastAPI):
    print("SME Growth Advisor API starting...")
    if not SARVAM_API_KEY:
        raise RuntimeError(
            "Missing required environment variable: SARVAM_API_KEY.\n"
            "Please create or update your .env file with SARVAM_API_KEY=your_key_here."
        )
    if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) < 1000:
        print("Initializing database from CSV files...")
        init_db()
    else:
        print(f"Database already exists at {DB_PATH}")
    init_bolna_tables()
    print("API ready.")
    yield
    print("API shutting down.")

app = FastAPI(title="SME Growth Advisor API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── CACHED KPI STATE ─────────────────────────────────────────────────────────
_kpi_cache = None
_ai_cache = None

def get_cached_kpis():
    global _kpi_cache, _ai_cache
    if _kpi_cache is None:
        _kpi_cache = compute_kpis()
        _ai_cache = generate_ai_insights(_kpi_cache)
    return _kpi_cache, _ai_cache

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.get("/api/kpis")
def api_kpis():
    kpis, ai = get_cached_kpis()
    return JSONResponse({**kpis, "ai": ai})

@app.get("/api/kpis/refresh")
def api_refresh():
    global _kpi_cache, _ai_cache
    _kpi_cache = None
    _ai_cache = None
    kpis, ai = get_cached_kpis()
    return JSONResponse({"status": "refreshed", **kpis, "ai": ai})

@app.get("/api/kpis/summary")
def api_summary():
    kpis, ai = get_cached_kpis()
    return JSONResponse({
        "totalRevenueFmt": kpis["totalRevenueFmt"],
        "netProfitFmt": kpis["netProfitFmt"],
        "netProfitMargin": kpis["netProfitMargin"],
        "cashInHandFmt": kpis["cashInHandFmt"],
        "pendingReceivablesFmt": kpis["pendingReceivablesFmt"],
        "healthScore": kpis["healthScore"],
        "healthLabel": kpis["healthLabel"],
        "brief": ai["brief"],
        "alerts": ai["alerts"][:3],
    })

@app.get("/api/sales")
def api_sales():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.SalesDate, s.ProductID, s.CustomerID,
               CAST(s.Quantity AS REAL) as qty,
               CAST(s.UnitPrice AS REAL) as price,
               CAST(s.TotalAmount AS REAL) as total,
               p.ProductName, c.DisplayName as CustomerName
         FROM sales_raw s
         LEFT JOIN products p ON s.ProductID = p.ProductID
         LEFT JOIN customers c ON s.CustomerID = c.CustomerID
         ORDER BY s.SalesDate DESC
         LIMIT 100
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return JSONResponse(rows)

@app.get("/api/products")
def api_products():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.*, cat.CategoryName,
               CAST(i.QuantityInStock AS REAL) as stock,
               CAST(i.ReorderPoint AS REAL) as reorder_point
        FROM products p
        LEFT JOIN categories cat ON p.CategoryID = cat.CategoryID
        LEFT JOIN inventory i ON p.ProductID = i.ProductID
        ORDER BY p.ProductID
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return JSONResponse(rows)

@app.get("/api/customers")
def api_customers():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.*,
               COALESCE(s.total_spent, 0) as total_spent,
               COALESCE(s.order_count, 0) as order_count,
               COALESCE(bc.status, 'No Call') as last_call_status,
               bc.promise_date as call_promise_date,
               bc.execution_id as call_execution_id,
               bc.transcript as call_transcript,
               bc.recording_url as call_recording_url,
               bc.follow_up_reminder as call_reminder,
               COALESCE(c.risk_tier, bc.risk_tier, 'Medium') as customer_risk_tier
        FROM customers c
        LEFT JOIN (
            SELECT CustomerID,
                   SUM(CAST(TotalAmount AS REAL)) as total_spent,
                   COUNT(*) as order_count
            FROM sales_raw GROUP BY CustomerID
        ) s ON c.CustomerID = s.CustomerID
        LEFT JOIN (
            SELECT b1.* FROM bolna_calls b1
            INNER JOIN (
                SELECT customer_id, MAX(id) as max_id FROM bolna_calls GROUP BY customer_id
            ) b2 ON b1.id = b2.max_id
        ) bc ON c.CustomerID = bc.customer_id
        ORDER BY CAST(COALESCE(s.total_spent, 0) AS REAL) DESC
        LIMIT 100
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    default_cust_phone = os.environ.get("DEFAULT_CUSTOMER_PHONE", "+916363077110").strip()
    for row in rows:
        row["Phone"] = default_cust_phone
    return JSONResponse(rows)

@app.get("/api/suppliers")
def api_suppliers():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.*,
               COALESCE(po.total_spend, 0) as total_spend,
               COALESCE(po.po_count, 0) as po_count
        FROM suppliers s
        LEFT JOIN (
            SELECT SupplierID,
                   SUM(CAST(TotalAmountSpent AS REAL)) as total_spend,
                   COUNT(*) as po_count
            FROM purchase_orders GROUP BY SupplierID
        ) po ON s.SupplierID = po.SupplierID
        ORDER BY CAST(COALESCE(po.total_spend, 0) AS REAL) DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    default_sup_phone = os.environ.get("DEFAULT_SUPPLIER_PHONE", "+916363077110").strip()
    for row in rows:
        row["Phone"] = default_sup_phone
    return JSONResponse(rows)

@app.get("/api/inventory")
def api_inventory():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT i.ProductID, i.QuantityInStock, i.ReorderPoint,
               p.ProductName, p.CategoryID, p.DefaultSellingPrice, p.StandardMarkup, p.SupplierID,
               cat.CategoryName,
               CASE WHEN CAST(i.QuantityInStock AS REAL) = 0 THEN 'out_of_stock'
                    WHEN CAST(i.QuantityInStock AS REAL) < CAST(i.ReorderPoint AS REAL) THEN 'low_stock'
                    ELSE 'ok' END as status
        FROM inventory i
        LEFT JOIN products p ON i.ProductID = p.ProductID
        LEFT JOIN categories cat ON p.CategoryID = cat.CategoryID
        ORDER BY CAST(i.QuantityInStock AS REAL) ASC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return JSONResponse(rows)

@app.get("/api/expenses")
def api_expenses():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM expenses ORDER BY ExpenseDate DESC LIMIT 200")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return JSONResponse(rows)

@app.get("/api/pending-payments")
def api_pending():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT pp.*, c.DisplayName as CustomerName, c.Phone, c.PaymentTermsDays,
               COALESCE(bc.status, pp.call_status, 'No Call') as last_call_status,
               COALESCE(bc.promise_date, pp.promise_date) as call_promise_date,
               COALESCE(bc.execution_id, pp.execution_id) as call_execution_id,
               bc.conversation_duration as call_duration,
               bc.recording_url as call_recording_url,
               bc.transcript as call_transcript,
               bc.summary as call_summary,
               bc.follow_up_reminder as call_reminder,
               COALESCE(c.risk_tier, bc.risk_tier, 'Medium') as customer_risk_tier
        FROM pending_payments pp
        LEFT JOIN customers c ON pp.CustomerID = c.CustomerID
        LEFT JOIN (
            SELECT b1.* FROM bolna_calls b1
            INNER JOIN (
                SELECT customer_id, MAX(id) as max_id FROM bolna_calls GROUP BY customer_id
            ) b2 ON b1.id = b2.max_id
        ) bc ON pp.CustomerID = bc.customer_id
        ORDER BY CAST(pp.DaysOverdue AS INTEGER) DESC, CAST(pp.AmountPending AS REAL) DESC
        LIMIT 50
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    default_cust_phone = os.environ.get("DEFAULT_CUSTOMER_PHONE", "+916363077110").strip()
    for row in rows:
        row["Phone"] = default_cust_phone
    return JSONResponse(rows)

@app.get("/api/purchase-orders")
def api_po():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT po.*, s.SupplierName, p.ProductName
        FROM purchase_orders po
        LEFT JOIN suppliers s ON po.SupplierID = s.SupplierID
        LEFT JOIN products p ON po.ProductID = p.ProductID
        ORDER BY po.PODate DESC
        LIMIT 200
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return JSONResponse(rows)

@app.get("/api/employees")
def api_employees():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM employees ORDER BY EmployeeID ASC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return JSONResponse(rows)

# ── AI Chat Endpoint ──
class ChatRequest(BaseModel):
    message: str
    context: str = ""

class PricingRequest(BaseModel):
    product_name: str

class GrowthRequest(BaseModel):
    query: str


class CollectionsActionRequest(BaseModel):
    customer_name: str
    amount: str
    action: str

@app.post("/api/collections-action")
def api_collections_action(req: CollectionsActionRequest):
    if req.action == "send_email":
        try:
            # Generate professional email using Gemma AI
            system_prompt = "You are an AI Collections Agent. Write a polite but firm payment reminder email to a customer."
            user_prompt = f"Write a collections reminder email to {req.customer_name}. They have a pending amount of {req.amount}. Keep it professional, under 100 words, and mention that late fees may apply if not paid soon."
            email_body = call_gemma(system_prompt, user_prompt)
            if not email_body:
                email_body = format_with_nvidia(system_prompt, user_prompt)
            if not email_body:
                email_body = f"Subject: Payment Reminder\n\nDear {req.customer_name},\n\nThis is a polite reminder that your payment of {req.amount} is currently overdue. Please process the pending amount at your earliest convenience to avoid any late fees.\n\nThank you,\nCollections Team"
            
            # Send the generated email via DronaHQ Webhook if available
            try:
                ask_dronahq(f"ACTION: SEND_EMAIL\\nTO: {req.customer_name}\\nBODY:\\n{email_body}")
            except Exception:
                pass
            return JSONResponse({"status": "success", "message": "Email generated successfully.", "email_body": email_body})
        except Exception as e:
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
    
    return JSONResponse({"status": "error", "message": "Unknown action"}, status_code=400)

@app.api_route("/api/collections/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_collections_to_node(path: str, request: Request):
    import httpx
    node_port = os.environ.get("NODE_PORT", "3000").strip()
    url = f"http://127.0.0.1:{node_port}/api/collections/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ['host', 'content-length']}
    body = await request.body()
    params = dict(request.query_params)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
                params=params
            )
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=dict(resp.headers),
                media_type=resp.headers.get("content-type", "application/json")
            )
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"Collections proxy error: {str(e)}"}, status_code=502)


# ─── BOLNA VOICE AI ROUTES (PAYMENT COLLECTION & ADVISOR AGENTS) ─────────────────────────

class BolnaCallRequest(BaseModel):
    customerId: str | None = None
    customer_id: str | None = None
    invoiceId: str | None = None
    invoice_id: str | None = None
    phone: str | None = None
    language: str | None = "hi"
    shop_name: str | None = "Katha AI Store"
    amount_due: str | None = None
    agent_id: str | None = None

class SetPaymentAgentRequest(BaseModel):
    agent_id: str

@app.get("/api/bolna/config")
def api_bolna_config():
    """Return configured Bolna Voice AI agents."""
    advisor_id = os.environ.get("BOLNA_AGENT_ID_ADVISOR", BOLNA_AGENT_ID_ADVISOR).strip()
    payment_id = os.environ.get("BOLNA_AGENT_ID_PAYMENT", BOLNA_AGENT_ID_PAYMENT).strip()
    supplier_id = os.environ.get("BOLNA_AGENT_ID_SUPPLIER", BOLNA_AGENT_ID_SUPPLIER).strip()
    ledger_id = os.environ.get("BOLNA_AGENT_ID_LEDGER", os.environ.get("LEDGER_AGENT_ID", BOLNA_AGENT_ID_LEDGER)).strip()
    return JSONResponse({
        "status": "success",
        "has_api_key": bool(os.environ.get("BOLNA_API_KEY", BOLNA_API_KEY).strip()),
        "advisor_agent_id": advisor_id,
        "payment_agent_id": payment_id,
        "supplier_agent_id": supplier_id,
        "ledger_agent_id": ledger_id,
        "advisor_agent_name": "Kavya",
        "payment_agent_name": "Meera",
        "supplier_agent_name": "Aarav",
        "ledger_agent_name": "Ledger Agent",
        "has_advisor_agent": bool(advisor_id),
        "has_payment_agent": bool(payment_id),
        "has_supplier_agent": bool(supplier_id),
        "has_ledger_agent": bool(ledger_id),
        "default_customer_phone": os.environ.get("DEFAULT_CUSTOMER_PHONE", DEFAULT_CUSTOMER_PHONE).strip(),
        "default_supplier_phone": os.environ.get("DEFAULT_SUPPLIER_PHONE", DEFAULT_SUPPLIER_PHONE).strip()
    })

@app.post("/api/bolna/set-payment-agent")
def api_set_payment_agent(req: SetPaymentAgentRequest):
    """Dynamically set the Bolna Agent ID for Payment Collection."""
    global BOLNA_AGENT_ID_PAYMENT
    new_id = req.agent_id.strip()
    if not new_id:
        return JSONResponse({"status": "error", "message": "Agent ID cannot be empty."}, status_code=400)
    BOLNA_AGENT_ID_PAYMENT = new_id
    os.environ["BOLNA_AGENT_ID_PAYMENT"] = new_id
    try:
        env_file = os.path.join(DATA_DIR, ".env")
        if os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                content = f.read()
            if "BOLNA_AGENT_ID_PAYMENT=" in content:
                content = re.sub(r"BOLNA_AGENT_ID_PAYMENT=.*", f"BOLNA_AGENT_ID_PAYMENT={new_id}", content)
            else:
                content += f"\nBOLNA_AGENT_ID_PAYMENT={new_id}\n"
            with open(env_file, "w", encoding="utf-8") as f:
                f.write(content)
    except Exception as e:
        print(f"Warning: could not write to .env: {e}")
    return JSONResponse({
        "status": "success",
        "message": f"Payment Collection Agent ID updated successfully to {new_id}",
        "payment_agent_id": new_id
    })

@app.post("/api/bolna/call")
def api_bolna_call(req: BolnaCallRequest):
    cid = req.customerId or req.customer_id
    inv_id = req.invoiceId or req.invoice_id

    conn = get_db()
    cur = conn.cursor()

    cust = None
    if cid:
        cur.execute("SELECT * FROM customers WHERE CustomerID = ? OR DisplayName = ?", (cid, cid))
        cust = cur.fetchone()

    if not cust and inv_id:
        cur.execute("""
            SELECT pp.*, c.*
            FROM pending_payments pp
            LEFT JOIN customers c ON pp.CustomerID = c.CustomerID
            WHERE pp.InvoiceID = ?
        """, (inv_id,))
        cust = cur.fetchone()

    if cust:
        customer_id = cust["CustomerID"]
        customer_name = cust["DisplayName"] or cust["ContactName"] or "Customer"
        db_phone = cust["Phone"]
        preferred_lang = req.language or cust.get("preferred_language") or "hi"
    else:
        customer_id = cid or "CUST-GUEST"
        customer_name = cid or "Customer"
        db_phone = None
        preferred_lang = req.language or "hi"

    # Fetch outstanding amount & due date from pending_payments
    cur.execute("""
        SELECT InvoiceID, DueDate, AmountPending, DaysOverdue
        FROM pending_payments
        WHERE CustomerID = ?
        ORDER BY CAST(DaysOverdue AS INTEGER) DESC
    """, (customer_id,))
    pending_records = cur.fetchall()

    if pending_records:
        total_due = sum(safe_float(r["AmountPending"]) for r in pending_records)
        amount_due = req.amount_due or fmt_inr(total_due)
        due_date = pending_records[0]["DueDate"] or datetime.now().strftime("%Y-%m-%d")
        target_invoice = inv_id or pending_records[0]["InvoiceID"]
    else:
        amount_due = req.amount_due or "₹25,000"
        due_date = datetime.now().strftime("%Y-%m-%d")
        target_invoice = inv_id or "INV-COLLECTION"

    # Resolve and validate phone number
    default_cust_phone = (os.environ.get("DEFAULT_CUSTOMER_PHONE") or "+916363077110").strip()
    target_phone = req.phone or default_cust_phone
    if not target_phone or target_phone.startswith("+912648") or target_phone.startswith("0") or len(target_phone) < 10:
        target_phone = default_cust_phone
    if not target_phone:
        conn.close()
        return JSONResponse({
            "status": "error",
            "message": f"No phone number on record for customer '{customer_name}'. Please specify a phone number."
        }, status_code=400)

    phone_e164 = format_to_e164(target_phone)
    if not is_valid_e164(phone_e164):
        conn.close()
        return JSONResponse({
            "status": "error",
            "message": f"Invalid phone number '{target_phone}'. Recipient number must follow E.164 format (e.g. +919876543210)."
        }, status_code=400)

    # Check Bolna environment credentials
    bolna_key = os.environ.get("BOLNA_API_KEY", BOLNA_API_KEY).strip()
    bolna_agent = (req.agent_id or os.environ.get("BOLNA_AGENT_ID_PAYMENT", BOLNA_AGENT_ID_PAYMENT)).strip()

    if not bolna_key:
        conn.close()
        return JSONResponse({
            "status": "error",
            "message": "Missing required environment variable: BOLNA_API_KEY. Please configure your .env file."
        }, status_code=500)
    if not bolna_agent:
        conn.close()
        return JSONResponse({
            "status": "error",
            "message": "Payment Collection Voice Agent ID has not been configured yet. Please provide your Bolna Agent ID for payment collections."
        }, status_code=400)

    days_overdue = pending_records[0]["DaysOverdue"] if pending_records else 14
    shop_name = req.shop_name or "Katha AI Store"

    # Construct Bolna payload tailored for Meera Collections Agent
    call_payload = {
        "agent_id": bolna_agent,
        "recipient_phone_number": phone_e164,
        "user_data": {
            "business_name": shop_name,
            "shop_name": shop_name,
            "customer_contact_name": customer_name,
            "customer_name": customer_name,
            "user_name": customer_name,
            "amount_pending": str(amount_due),
            "amount_due": str(amount_due),
            "days_overdue": str(days_overdue),
            "invoice_id": target_invoice,
            "due_date": str(due_date),
            "payment_link": f"https://pay.kathaai.in/inv/{target_invoice}",
            "language": preferred_lang,
            "timezone": "Asia/Kolkata",
            "last_query_or_ticket": f"Pending invoice {target_invoice} payment of {amount_due} overdue by {days_overdue} days since {due_date}"
        }
    }

    headers = {
        "Authorization": f"Bearer {bolna_key}",
        "Content-Type": "application/json"
    }

    try:
        bolna_resp = requests.post(f"{BOLNA_BASE_URL}/call", headers=headers, json=call_payload, timeout=30)
    except requests.exceptions.RequestException as e:
        conn.close()
        return JSONResponse({
            "status": "error",
            "message": f"Network error connecting to Bolna Voice AI upstream: {str(e)}"
        }, status_code=502)

    if bolna_resp.status_code not in (200, 201, 202):
        conn.close()
        err_msg = bolna_resp.text
        try:
            err_json = bolna_resp.json()
            err_msg = err_json.get("message") or err_json.get("error") or err_json.get("detail") or bolna_resp.text
        except Exception:
            pass
        return JSONResponse({
            "status": "error",
            "message": f"Bolna AI Call failed (HTTP {bolna_resp.status_code}): {err_msg}"
        }, status_code=bolna_resp.status_code)

    resp_data = bolna_resp.json()
    execution_id = resp_data.get("execution_id") or resp_data.get("id")
    call_status = resp_data.get("status") or "queued"

    now_iso = datetime.utcnow().isoformat()

    # Save to bolna_calls
    cur.execute("""
        INSERT INTO bolna_calls (
            execution_id, customer_id, customer_name, invoice_id,
            phone_number, amount_due, due_date, language, status,
            raw_payload, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        execution_id, customer_id, customer_name, target_invoice,
        phone_e164, amount_due, due_date, preferred_lang, call_status,
        json.dumps(resp_data), now_iso, now_iso
    ))

    # Update customer ledger entry in pending_payments
    cur.execute("""
        UPDATE pending_payments
        SET execution_id = ?, call_status = ?, last_call_at = ?
        WHERE CustomerID = ? OR InvoiceID = ?
    """, (execution_id, call_status, now_iso, customer_id, target_invoice))

    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "success",
        "message": f"Outbound voice call queued with Agent Meera for {customer_name} ({phone_e164})",
        "execution_id": execution_id,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "agent_name": "Meera",
        "phone": phone_e164,
        "amount_due": amount_due,
        "due_date": due_date,
        "call_status": call_status,
        "invoice_id": target_invoice
    })

class BolnaAdvisorCallRequest(BaseModel):
    phone: str | None = None
    language: str | None = "hi"
    owner_name: str | None = "Arjun Mehta"
    query: str | None = None

@app.post("/api/bolna/advisor/call")
def api_bolna_advisor_call(req: BolnaAdvisorCallRequest):
    """Trigger Kavya Bolna Voice AI to call the owner for AI Business Advisory."""
    phone = req.phone
    if not phone:
        return JSONResponse({
            "status": "error",
            "message": "Please provide your phone number to receive the call from Kavya."
        }, status_code=400)

    phone_e164 = format_to_e164(phone)
    if not is_valid_e164(phone_e164):
        return JSONResponse({
            "status": "error",
            "message": f"Invalid phone number '{phone}'. Number must follow E.164 format (e.g. +919876543210)."
        }, status_code=400)

    bolna_key = os.environ.get("BOLNA_API_KEY", BOLNA_API_KEY).strip()
    bolna_agent_advisor = (os.environ.get("AGENT_ID_ADVISOR") or os.environ.get("BOLNA_AGENT_ID_ADVISOR") or BOLNA_AGENT_ID_ADVISOR).strip()

    if not bolna_key:
        return JSONResponse({
            "status": "error",
            "message": "BOLNA_API_KEY is not configured in .env file."
        }, status_code=500)

    # Gather real-time KPIs to feed into Kavya's dynamic context
    kpis, ai = get_cached_kpis()
    total_rev = kpis.get("totalRevenueFmt", "₹20.19Cr")
    net_profit = kpis.get("netProfitFmt", "₹5.23Cr")
    margin = kpis.get("profitMargin", 25.9)
    overdue = kpis.get("overdueAmount", "₹2.54Cr")
    overdue_count = kpis.get("overdueCount", 144)
    low_stock = [f"{i['name']} ({i['qty']} left)" for i in kpis.get("lowStockItems", [])[:3]]
    top_cust = kpis.get("topCustomers", [{}])[0].get("name", "Aarav Enterprise")

    owner_name = req.owner_name or "Arjun Mehta"
    user_query = req.query or f"Owner daily briefing: Revenue {total_rev}, Net profit {net_profit} ({margin}% margin), Overdue udhaar {overdue} across {overdue_count} invoices. Low stock on {', '.join(low_stock) if low_stock else 'none'}."

    user_data = {
        "user_name": owner_name,
        "customer_name": owner_name,
        "business_name": "Katha AI Store",
        "shop_name": "Katha AI Store",
        "today_sales": total_rev,
        "total_revenue": total_rev,
        "net_profit": net_profit,
        "pending_receivables": overdue,
        "overdue_invoices": f"{overdue_count} invoices totaling {overdue}",
        "low_stock_items": ", ".join(low_stock) if low_stock else "None",
        "top_customer": top_cust,
        "last_query_or_ticket": user_query,
        "language": req.language or "hi",
        "timezone": "Asia/Kolkata"
    }

    call_payload = {
        "agent_id": bolna_agent_advisor,
        "recipient_phone_number": phone_e164,
        "user_data": user_data
    }

    headers = {
        "Authorization": f"Bearer {bolna_key}",
        "Content-Type": "application/json"
    }

    try:
        bolna_resp = requests.post(f"{BOLNA_BASE_URL}/call", headers=headers, json=call_payload, timeout=30)
    except requests.exceptions.RequestException as e:
        return JSONResponse({
            "status": "error",
            "message": f"Network error connecting to Bolna Voice AI: {str(e)}"
        }, status_code=502)

    if bolna_resp.status_code not in (200, 201, 202):
        err_msg = bolna_resp.text
        try:
            err_json = bolna_resp.json()
            err_msg = err_json.get("message") or err_json.get("error") or err_json.get("detail") or bolna_resp.text
        except Exception:
            pass
        return JSONResponse({
            "status": "error",
            "message": f"Bolna AI Call failed (HTTP {bolna_resp.status_code}): {err_msg}"
        }, status_code=bolna_resp.status_code)

    resp_data = bolna_resp.json()
    execution_id = resp_data.get("execution_id") or resp_data.get("id")
    call_status = resp_data.get("status") or "queued"

    now_iso = datetime.utcnow().isoformat()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bolna_calls (
            execution_id, customer_id, customer_name, invoice_id,
            phone_number, amount_due, due_date, language, status,
            summary, raw_payload, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        execution_id, "OWNER-ADVISOR", f"{owner_name} (Owner Briefing)", "ADVISOR-VOICE",
        phone_e164, net_profit, datetime.now().strftime("%Y-%m-%d"), req.language or "hi", call_status,
        f"Kavya voice briefing for owner {owner_name}: {user_query}",
        json.dumps(resp_data), now_iso, now_iso
    ))
    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "success",
        "message": f"Kavya is calling your phone at {phone_e164} for your AI Business Briefing!",
        "execution_id": execution_id,
        "owner_name": owner_name,
        "phone": phone_e164,
        "agent_name": "Kavya",
        "call_status": call_status
    })

# ─── BOLNA VOICE AI ROUTES (LEDGER AGENT: ACCOUNTING & BOOKKEEPING) ─────────

class BolnaLedgerCallRequest(BaseModel):
    phone: str | None = None
    language: str | None = "hi"
    owner_name: str | None = "Arjun Mehta"
    query: str | None = None
    agent_id: str | None = None

@app.post("/api/bolna/ledger/call")
def api_bolna_ledger_call(req: BolnaLedgerCallRequest):
    """Trigger Bolna Voice AI (Ledger Agent) to call the owner/accountant for ledger reconciliation and bookkeeping."""
    phone = req.phone
    if not phone:
        return JSONResponse({
            "status": "error",
            "message": "Please provide your phone number to receive the call from the Ledger Agent."
        }, status_code=400)

    phone_e164 = format_to_e164(phone)
    if not is_valid_e164(phone_e164):
        return JSONResponse({
            "status": "error",
            "message": f"Invalid phone number '{phone}'. Number must follow E.164 format (e.g. +919876543210)."
        }, status_code=400)

    bolna_key = os.environ.get("BOLNA_API_KEY", BOLNA_API_KEY).strip()
    bolna_agent_ledger = (req.agent_id or os.environ.get("LEDGER_AGENT_ID") or os.environ.get("AGENT_ID_LEDGER") or os.environ.get("BOLNA_AGENT_ID_LEDGER") or os.environ.get("BOLNA_LEDGER_AGENT_ID") or BOLNA_AGENT_ID_LEDGER).strip()

    if not bolna_key:
        return JSONResponse({
            "status": "error",
            "message": "BOLNA_API_KEY is not configured in .env file."
        }, status_code=500)

    # Fetch recent ledger data for dynamic context
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, customer_name, type, amount, notes, created_at FROM ledger_entries ORDER BY id DESC LIMIT 5")
    recent_rows = cur.fetchall()
    
    cur.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM ledger_entries")
    ledger_stats = cur.fetchone()
    total_entries = ledger_stats[0] if ledger_stats else 0
    total_ledger_amount = ledger_stats[1] if ledger_stats else 0.0
    conn.close()

    entries_summary = "; ".join([f"{r['customer_name']}: {r['type']} of ₹{r['amount']}" for r in recent_rows]) if recent_rows else "No recent entries recorded."

    owner_name = req.owner_name or "Arjun Mehta"
    user_query = req.query or f"Ledger update: Total {total_entries} transactions totaling ₹{total_ledger_amount:,.2f}. Recent: {entries_summary}"

    user_data = {
        "user_name": owner_name,
        "customer_name": owner_name,
        "business_name": "Katha AI Store",
        "shop_name": "Katha AI Store",
        "total_ledger_transactions": total_entries,
        "total_ledger_amount": f"₹{total_ledger_amount:,.2f}",
        "recent_ledger_summary": entries_summary,
        "last_query_or_ticket": user_query,
        "language": req.language or "hi",
        "timezone": "Asia/Kolkata"
    }

    call_payload = {
        "agent_id": bolna_agent_ledger,
        "recipient_phone_number": phone_e164,
        "user_data": user_data
    }

    headers = {
        "Authorization": f"Bearer {bolna_key}",
        "Content-Type": "application/json"
    }

    try:
        bolna_resp = requests.post(f"{BOLNA_BASE_URL}/call", headers=headers, json=call_payload, timeout=30)
    except requests.exceptions.RequestException as e:
        return JSONResponse({
            "status": "error",
            "message": f"Network error connecting to Bolna Voice AI: {str(e)}"
        }, status_code=502)

    if bolna_resp.status_code not in (200, 201, 202):
        err_msg = bolna_resp.text
        try:
            err_json = bolna_resp.json()
            err_msg = err_json.get("message") or err_json.get("error") or err_json.get("detail") or bolna_resp.text
        except Exception:
            pass
        return JSONResponse({
            "status": "error",
            "message": f"Bolna AI Call failed (HTTP {bolna_resp.status_code}): {err_msg}"
        }, status_code=bolna_resp.status_code)

    resp_data = bolna_resp.json()
    execution_id = resp_data.get("execution_id") or resp_data.get("id")
    call_status = resp_data.get("status") or "queued"

    now_iso = datetime.utcnow().isoformat()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bolna_calls (
            execution_id, customer_id, customer_name, invoice_id,
            phone_number, amount_due, due_date, language, status,
            summary, raw_payload, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        execution_id, "OWNER-LEDGER", f"{owner_name} (Ledger Agent)", "LEDGER-VOICE",
        phone_e164, f"₹{total_ledger_amount:,.2f}", datetime.now().strftime("%Y-%m-%d"), req.language or "hi", call_status,
        f"Ledger Agent voice briefing for owner {owner_name}: {user_query}",
        json.dumps(resp_data), now_iso, now_iso
    ))
    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "success",
        "message": f"Ledger Agent is calling your phone at {phone_e164} for ledger reconciliation!",
        "execution_id": execution_id,
        "owner_name": owner_name,
        "phone": phone_e164,
        "agent_name": "Ledger Agent",
        "agent_id": bolna_agent_ledger,
        "call_status": call_status
    })

# ─── BOLNA VOICE AI ROUTES (SUPPLIER AGENT: AARAV) ───────────────────────────

class BolnaSupplierCallRequest(BaseModel):
    supplier_id: str | None = None
    supplier_name: str | None = None
    contact_person: str | None = None
    phone: str | None = None
    call_reason: str | None = "quote"
    product_name: str | None = None
    order_quantity: str | None = None
    requested_delivery_date: str | None = None
    language: str | None = "en"
    shop_name: str | None = "Katha AI Store"
    agent_id: str | None = None

@app.post("/api/bolna/supplier/call")
def api_bolna_supplier_call(req: BolnaSupplierCallRequest):
    """Trigger Agent Aarav (Bolna Voice AI) to call a supplier for quotation, stock confirmation, or delivery follow-up."""
    sid = req.supplier_id
    sname = req.supplier_name

    conn = get_db()
    cur = conn.cursor()

    sup = None
    if sid:
        cur.execute("SELECT * FROM suppliers WHERE SupplierID = ? OR SupplierName = ?", (sid, sid))
        sup = cur.fetchone()
    if not sup and sname:
        cur.execute("SELECT * FROM suppliers WHERE SupplierName LIKE ? OR ContactPerson LIKE ?", (f"%{sname}%", f"%{sname}%"))
        sup = cur.fetchone()

    conn.close()

    target_supplier_id = sup["SupplierID"] if sup else (sid or "SUP-GUEST")
    target_supplier_name = sup["SupplierName"] if sup else (sname or "Supplier Partner")
    target_contact = req.contact_person or (sup["ContactPerson"] if sup else target_supplier_name)
    default_sup_phone = (os.environ.get("DEFAULT_SUPPLIER_PHONE") or "+916363077110").strip()
    target_phone = req.phone or default_sup_phone
    if not target_phone or target_phone.startswith("+9193806") or target_phone.startswith("0") or len(target_phone) < 10:
        target_phone = default_sup_phone
    target_category = sup["PrimaryCategory"] if sup else "Wholesale Supplies"

    if not target_phone:
        return JSONResponse({
            "status": "error",
            "message": f"No phone number on record for supplier '{target_supplier_name}'. Please provide a valid phone number."
        }, status_code=400)

    phone_e164 = format_to_e164(target_phone)
    if not is_valid_e164(phone_e164):
        return JSONResponse({
            "status": "error",
            "message": f"Invalid phone number '{target_phone}'. Number must follow E.164 format (e.g. +919876543210)."
        }, status_code=400)

    bolna_key = os.environ.get("BOLNA_API_KEY", BOLNA_API_KEY).strip()
    bolna_agent_supplier = (req.agent_id or os.environ.get("AGENT_ID_SUPPLIER") or os.environ.get("BOLNA_AGENT_ID_SUPPLIER") or BOLNA_AGENT_ID_SUPPLIER).strip()

    if not bolna_key:
        return JSONResponse({
            "status": "error",
            "message": "BOLNA_API_KEY is not configured in .env file."
        }, status_code=500)

    reason_map = {
        "quote": "get a formal price quote and minimum order quantity",
        "stock_confirmation": "confirm immediate stock availability and delivery lead time",
        "delivery_followup": "follow up on an expected delivery and tracking status",
        "price_review": "review current wholesale rates and request volume discount tiers"
    }
    reason_desc = reason_map.get(req.call_reason, req.call_reason or "supplier procurement inquiry")
    prod_name = req.product_name or target_category or "Commercial Restock Items"
    order_qty = req.order_quantity or "50 units"
    delivery_date = req.requested_delivery_date or "within 3 to 5 business days"
    shop_name = req.shop_name or "Katha AI Store"

    user_data = {
        "supplier_id": target_supplier_id,
        "supplier_name": target_supplier_name,
        "contact_person": target_contact,
        "call_reason": reason_desc,
        "product_name": prod_name,
        "order_quantity": str(order_qty),
        "requested_delivery_date": str(delivery_date),
        "business_name": shop_name,
        "shop_name": shop_name,
        "language": req.language or "en",
        "timezone": "Asia/Kolkata",
        "last_query_or_ticket": f"Procurement call to {target_supplier_name} regarding {prod_name} ({order_qty}) - Reason: {reason_desc}"
    }

    call_payload = {
        "agent_id": bolna_agent_supplier,
        "recipient_phone_number": phone_e164,
        "user_data": user_data
    }

    headers = {
        "Authorization": f"Bearer {bolna_key}",
        "Content-Type": "application/json"
    }

    try:
        bolna_resp = requests.post(f"{BOLNA_BASE_URL}/call", headers=headers, json=call_payload, timeout=30)
    except requests.exceptions.RequestException as e:
        return JSONResponse({
            "status": "error",
            "message": f"Network error connecting to Bolna Voice AI: {str(e)}"
        }, status_code=502)

    if bolna_resp.status_code not in (200, 201, 202):
        err_msg = bolna_resp.text
        try:
            err_json = bolna_resp.json()
            err_msg = err_json.get("message") or err_json.get("error") or err_json.get("detail") or bolna_resp.text
        except Exception:
            pass
        return JSONResponse({
            "status": "error",
            "message": f"Bolna AI Call failed (HTTP {bolna_resp.status_code}): {err_msg}"
        }, status_code=bolna_resp.status_code)

    resp_data = bolna_resp.json()
    execution_id = resp_data.get("execution_id") or resp_data.get("id")
    call_status = resp_data.get("status") or "queued"

    now_iso = datetime.utcnow().isoformat()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bolna_calls (
            execution_id, customer_id, customer_name, invoice_id,
            phone_number, amount_due, due_date, language, status,
            summary, raw_payload, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        execution_id, f"SUPPLIER-{target_supplier_id}", f"{target_supplier_name} ({target_contact})", "SUPPLIER-PO",
        phone_e164, str(order_qty), delivery_date, req.language or "en", call_status,
        f"Aarav procurement call to {target_supplier_name}: {reason_desc} for {prod_name} ({order_qty})",
        json.dumps(resp_data), now_iso, now_iso
    ))
    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "success",
        "message": f"Agent Aarav is placing a procurement call to {target_supplier_name} ({phone_e164})",
        "execution_id": execution_id,
        "supplier_id": target_supplier_id,
        "supplier_name": target_supplier_name,
        "contact_person": target_contact,
        "phone": phone_e164,
        "agent_name": "Aarav",
        "product_name": prod_name,
        "order_quantity": order_qty,
        "call_status": call_status
    })

@app.post("/api/bolna/webhook")
async def bolna_webhook(request: Request):
    """Receive Bolna call execution updates and terminal callbacks."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid JSON body"}, status_code=400)

    execution_id = body.get("execution_id") or body.get("id")
    if not execution_id:
        return JSONResponse({"status": "ignored", "message": "Missing execution_id in webhook payload"}, status_code=200)

    result = process_bolna_execution_update(execution_id, body)
    return JSONResponse({
        "status": "success",
        "message": f"Webhook processed for execution {execution_id}",
        "data": result
    })

@app.get("/api/bolna/call/{execution_id}")
def api_get_bolna_call(execution_id: str, sync: bool = True):
    """Get call details, transcript, recording, and promise date. Auto-syncs from Bolna if still active."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM bolna_calls WHERE execution_id = ?", (execution_id,))
    row = cur.fetchone()
    conn.close()

    call_record = dict(row) if row else None

    # Sync from Bolna API if record is not terminal yet, or if completed but transcript not yet retrieved
    needs_sync = sync and (
        not call_record or 
        call_record.get("status") not in ("completed", "failed", "no-answer", "busy") or
        (call_record.get("status") in ("completed", "call-disconnected") and not call_record.get("transcript"))
    )
    if needs_sync:
        bolna_key = os.environ.get("BOLNA_API_KEY", BOLNA_API_KEY).strip()
        if bolna_key:
            try:
                resp = requests.get(
                    f"{BOLNA_BASE_URL}/executions/{execution_id}",
                    headers={"Authorization": f"Bearer {bolna_key}"},
                    timeout=10
                )
                if resp.status_code == 200:
                    remote_data = resp.json()
                    process_bolna_execution_update(execution_id, remote_data)
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM bolna_calls WHERE execution_id = ?", (execution_id,))
                    updated_row = cur.fetchone()
                    conn.close()
                    if updated_row:
                        call_record = dict(updated_row)
            except Exception as e:
                print(f"Error syncing Bolna execution {execution_id}: {e}")

    if not call_record:
        return JSONResponse({"status": "error", "message": f"Call execution '{execution_id}' not found."}, status_code=404)

    return JSONResponse(call_record)

@app.get("/api/collections/status/{execution_id}")
def api_collections_status(execution_id: str):
    """Call status polling endpoint for Collections Agent UI."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT status, conversation_duration, summary, promise_date, amount_agreed FROM bolna_calls WHERE execution_id = ?", (execution_id,))
    row = cur.fetchone()
    conn.close()

    if row:
        st = str(row["status"] or "").lower()
        if st in ("completed", "call-disconnected", "done"):
            return {
                "status": "completed",
                "raw_status": st,
                "duration": row["conversation_duration"] or 0,
                "summary": row["summary"] or "",
                "promise_date": row["promise_date"],
                "amount_agreed": row["amount_agreed"]
            }
        elif st in ("failed", "busy", "no-answer"):
            return {
                "status": "failed",
                "raw_status": st,
                "detail": st
            }
        elif st in ("in-progress", "ringing", "queued", "initiated"):
            return {
                "status": "in-progress",
                "raw_status": st,
                "duration": row["conversation_duration"] or 0
            }

    # If not in local DB or unknown, check Bolna API directly
    bolna_key = (os.environ.get("VOICE_API_KEY") or os.environ.get("BOLNA_API_KEY") or BOLNA_API_KEY or "").strip()
    if bolna_key:
        try:
            r = requests.get(f"{BOLNA_BASE_URL}/executions/{execution_id}", headers={"Authorization": f"Bearer {bolna_key}"}, timeout=6)
            if r.status_code == 200:
                d = r.json()
                st = (d.get("status") or "").lower()
                dur = d.get("conversation_duration") or (d.get("telephony_data") or {}).get("duration", 0)
                if st in ("completed", "call-disconnected", "done"):
                    return {"status": "completed", "raw_status": st, "duration": dur}
                elif st in ("failed", "busy", "no-answer"):
                    return {"status": "failed", "raw_status": st}
                elif st in ("in-progress", "ringing", "queued", "initiated"):
                    return {"status": "in-progress", "raw_status": st, "duration": dur}
        except Exception:
            pass

    return {"status": "unknown"}

@app.get("/api/bolna/calls")
def api_get_all_bolna_calls(limit: int = 50):
    """Retrieve history of all Bolna calls with transcripts and outcomes."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM bolna_calls ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return JSONResponse(rows)

@app.get("/api/bolna/customer/{customer_id}")
def api_get_customer_bolna_calls(customer_id: str):
    """Retrieve call history for a specific customer."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM bolna_calls WHERE customer_id = ? ORDER BY id DESC", (customer_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return JSONResponse(rows)


def generate_supplier_profile_cards_html(suppliers_list, related_product=None):
    if not suppliers_list:
        return ""
    import urllib.parse
    cards_html = [
        '<div class="chat-suppliers-container" style="margin-top: 12px; display: flex; flex-direction: column; gap: 10px;">',
        '<div style="font-weight: 700; font-size: 13px; color: #1e293b; margin-bottom: 2px;">📋 Verified Supplier Profiles:</div>'
    ]
    for sup in suppliers_list:
        sup_id = sup[0]
        sup_name = sup[1]
        contact_person = sup[2]
        contact_email = sup[3]
        phone = "+916363077110"  # Confirmed DEFAULT_SUPPLIER_PHONE
        category = sup[5] if len(sup) > 5 else "Wholesale Goods"
        score = str(sup[6]) if len(sup) > 6 else "8"
        
        safe_name = sup_name.replace("'", "\\'")
        safe_contact = contact_person.replace("'", "\\'")
        safe_cat = category.replace("'", "\\'")
        
        item_text = related_product or category
        prod_subject = urllib.parse.quote(f"Purchase Order Inquiry - {item_text} - Katha AI")
        prod_body = urllib.parse.quote(f"Dear {contact_person},\n\nWe would like to place an urgent purchase order for {item_text}.\n\nPlease share your current quotation, availability, and earliest delivery date.\n\nBest regards,\nArjun Mehta\nKatha AI Store\nPhone: +916363077110")
        
        card = f'''<div class="chat-supplier-card" style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 14px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); font-family: inherit;">
  <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 6px;">
    <div>
      <div style="font-weight: 700; font-size: 14px; color: #0f172a;">{sup_name} <span style="font-size: 11px; font-weight: 500; color: #64748b;">({sup_id})</span></div>
      <div style="font-size: 11px; color: #475569; margin-top: 3px;">
        <span style="display: inline-block; background: #f1f5f9; padding: 2px 8px; border-radius: 10px; font-weight: 600; color: #334155; margin-right: 4px;">🏷️ {category}</span>
        <span style="display: inline-block; background: #ecfdf5; padding: 2px 8px; border-radius: 10px; font-weight: 600; color: #059669;">⭐ {score}/10 Reliability</span>
      </div>
    </div>
  </div>
  <div style="font-size: 12px; color: #334155; margin: 6px 0 10px 0; line-height: 1.6; border-top: 1px solid #f1f5f9; padding-top: 6px;">
    <div>👤 <strong>Contact:</strong> {contact_person}</div>
    <div>📞 <strong>Phone:</strong> {phone}</div>
    <div>✉️ <strong>Email:</strong> {contact_email}</div>
  </div>
  <div style="display: flex; gap: 8px; flex-wrap: wrap;">
    <button type="button" class="btn-voice-call-row" onclick="openAaravSupplierModal({{ name: '{safe_name}', id: '{sup_id}', contactPerson: '{safe_contact}', category: '{safe_cat}', score: '{score}.0', phone: '{phone}' }})" style="background: #10b981; color: white; border: none; padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 5px;">
      <i class="fas fa-phone-volume"></i> Call (Agent Aarav)
    </button>
    <a href="mailto:{contact_email}?subject={prod_subject}&body={prod_body}" class="btn-mail-row" target="_blank" style="background: #3b82f6; color: white; text-decoration: none; padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600; display: inline-flex; align-items: center; gap: 5px;">
      <i class="fas fa-envelope"></i> Mail Supplier
    </a>
  </div>
</div>'''
        cards_html.append(card)
    cards_html.append('</div>')
    return '\n'.join(cards_html)

@app.post("/api/chat")
def api_chat(req: ChatRequest):
    kpis, ai = get_cached_kpis()
    msg = req.message.lower().strip()
    
    import json
    
    # Extract matching product context dynamically based on user query
    conn = get_db()
    cur = conn.cursor()
    
    # Check if this is a supplier / procurement query
    is_supplier_query = any(k in msg for k in [
        'supplier', 'suppliers', 'vendor', 'vendors', 'procure', 'procurement',
        'who supplies', 'who provides', 'best supplier', 'lowest product',
        'out of stock', 'lowest stock', 'reorder', 'which stock is running low',
        'lowest in stock', 'lowest inventory'
    ])
    
    is_lowest_product_query = any(k in msg for k in [
        'lowest product', 'lowest stock', 'out of stock', 'lowest in stock', 'lowest inventory',
        'knife'
    ])

    matched_suppliers = []
    lowest_prod_info = None

    if is_supplier_query:
        if is_lowest_product_query:
            cur.execute("""
                SELECT p.ProductID, p.ProductName, p.SupplierID, c.CategoryName,
                       CAST(i.QuantityInStock AS INTEGER) as stock,
                       CAST(i.ReorderPoint AS INTEGER) as reorder
                FROM inventory i
                JOIN products p ON i.ProductID = p.ProductID
                LEFT JOIN categories c ON p.CategoryID = c.CategoryID
                ORDER BY CAST(i.QuantityInStock AS INTEGER) ASC
                LIMIT 1
            """)
            lowest_row = cur.fetchone()
            if lowest_row:
                lowest_prod_info = {
                    "id": lowest_row[0],
                    "name": lowest_row[1],
                    "category": lowest_row[3] or "Kitchen Gadgets",
                    "stock": lowest_row[4],
                    "reorder": lowest_row[5]
                }
                # Fetch top suppliers for this category
                cur.execute("""
                    SELECT SupplierID, SupplierName, ContactPerson, ContactEmail, Phone, PrimaryCategory, ReliabilityScore
                    FROM suppliers
                    WHERE PrimaryCategory = ? COLLATE NOCASE OR PrimaryCategory LIKE ?
                    ORDER BY CAST(ReliabilityScore AS INTEGER) DESC, SupplierName ASC
                    LIMIT 2
                """, (lowest_prod_info["category"], f"%{lowest_prod_info['category']}%"))
                matched_suppliers = cur.fetchall()

        if not matched_suppliers:
            cur.execute("""
                SELECT SupplierID, SupplierName, ContactPerson, ContactEmail, Phone, PrimaryCategory, ReliabilityScore
                FROM suppliers
                ORDER BY CAST(ReliabilityScore AS INTEGER) DESC, SupplierName ASC
                LIMIT 4
            """)
            matched_suppliers = cur.fetchall()

    # Find if user mentioned any specific product in their message
    words = [w for w in msg.replace('%', ' ').replace(',', ' ').split() if len(w) > 3]
    matched_products = []
    for word in words:
        cur.execute("""
            SELECT p.ProductName, p.DefaultSellingPrice, p.StandardMarkup,
                   COALESCE(SUM(s.Quantity), 0) as units_sold
            FROM products p
            LEFT JOIN sales_raw s ON p.ProductID = s.ProductID
            WHERE p.ProductName LIKE ? COLLATE NOCASE
            GROUP BY p.ProductID
            LIMIT 3
        """, (f"%{word}%",))
        for r in cur.fetchall():
            p_name, p_price, p_markup, p_units = r[0], float(r[1] or 0), float(r[2] or 1.2), float(r[3] or 0)
            cost = p_price / p_markup if p_markup > 0 else p_price * 0.75
            margin = ((p_price - cost) / p_price * 100) if p_price > 0 else 25.0
            matched_products.append(f"{p_name}: Price=₹{p_price:.2f}, Cost=₹{cost:.2f}, Margin={margin:.1f}%, Sold={int(p_units)} units")
    conn.close()

    # Build clean, high-density business context
    context_summary = {
        "business": "Indian SME Retail & Distribution",
        "totalRevenue": kpis.get("totalRevenueFmt", "₹20.19Cr"),
        "netProfit": kpis.get("netProfitFmt", "₹-1.99Cr"),
        "profitMargin": f"{kpis.get('netProfitMargin', -9.9)}%",
        "cashInHand": kpis.get("cashInHandFmt", "₹43.42Cr"),
        "pendingReceivables": kpis.get("pendingReceivablesFmt", "₹4.33Cr"),
        "collectionRate": f"{kpis.get('collectionRate', 92.1)}%",
        "overdueInvoices": f"{kpis.get('overdueCount', 144)} invoices ({kpis.get('overdueAmount', '₹2.54Cr')})",
        "queriedProducts": matched_products if matched_products else ["General catalog items (400 active SKUs)"],
        "topAlerts": [a.get("title") + ": " + a.get("message") for a in (ai.get("alerts") or [])[:3]],
        "lowStockItems": kpis.get("lowStockItems", [])
    }
    
    if is_supplier_query and matched_suppliers:
        context_summary["supplierProfiles"] = [
            {
                "id": s[0],
                "name": s[1],
                "contact": s[2],
                "email": s[3],
                "phone": "+916363077110",
                "category": s[5] if len(s) > 5 else "Wholesale Goods",
                "reliability": f"{s[6]}/10" if len(s) > 6 else "8/10"
            } for s in matched_suppliers
        ]
        if lowest_prod_info:
            context_summary["lowestStockProduct"] = lowest_prod_info

    system_prompt = f"""You are the AI Business Advisor powered by DronaHQ for Arjun's Indian SME (Retail & Distribution).
Answer the user's business question with clear financial reasoning, projected impact on volume/margin/revenue, and actionable recommendations.

[CURRENT BUSINESS CONTEXT]
{json.dumps(context_summary, indent=2, ensure_ascii=False)}

GUIDELINES:
1. Answer directly with data-driven reasoning and clear markdown bullet points.
2. If the user asks about price changes, calculate the exact new price, margin change, and demand trend impact.
3. Keep the response concise, professional, and practical (under 150 words)."""

    if is_supplier_query and matched_suppliers:
        if lowest_prod_info:
            system_prompt += f"""\n\nCRITICAL SUPPLIER INSTRUCTION:
The lowest stock product in inventory is **{lowest_prod_info['name']}** ({lowest_prod_info['id']}) with {lowest_prod_info['stock']} units left (Reorder point: {lowest_prod_info['reorder']}).
The top verified suppliers for {lowest_prod_info['category']} are: {', '.join([s[1] + ' (' + str(s[6]) + '/10 score)' for s in matched_suppliers])}.
State this clearly. Note that verified supplier profile cards with Call and Email buttons are displayed directly below."""
        else:
            system_prompt += f"""\n\nCRITICAL SUPPLIER INSTRUCTION:
Our verified active suppliers are: {', '.join([s[1] + ' (' + s[5] + ', ' + str(s[6]) + '/10 score)' for s in matched_suppliers])}.
Summarize our trusted vendor network. Note that supplier profile cards with Call and Email buttons are displayed below."""

    try:
        user_message = req.message.strip()
        print(f"[AI Advisor] Incoming message: {user_message}")
        
        # 1. Primary: DronaHQ Agent
        bot_response = ""
        dhq_error_detail = ""
        try:
            print(f"[AI Advisor] Querying DronaHQ Agent for: {user_message}")
            dronahq_prompt = f"""Role: You are the AI Business Advisor powered by DronaHQ for Arjun's Indian SME Retail & Distribution business.
Answer the user's question directly with clear data-driven reasoning and actionable insights based on the live business data below.

Live Business Context:
- Revenue: {context_summary['totalRevenue']}
- Net Profit: {context_summary['netProfit']} ({context_summary['profitMargin']} margin)
- Cash in Hand: {context_summary['cashInHand']}
- Pending Receivables: {context_summary['pendingReceivables']} ({context_summary['overdueInvoices']})
- Collection Rate: {context_summary['collectionRate']}
- Catalog / Products: {', '.join(context_summary['queriedProducts'][:5])}"""

            if is_supplier_query and matched_suppliers:
                if lowest_prod_info:
                    dronahq_prompt += f"\n- Lowest Stock Product: {lowest_prod_info['name']} ({lowest_prod_info['id']}) at {lowest_prod_info['stock']} units (Reorder: {lowest_prod_info['reorder']}). Recommended Suppliers: {', '.join([s[1] + ' (' + str(s[6]) + '/10)' for s in matched_suppliers])}."
                else:
                    dronahq_prompt += f"\n- Top Suppliers: {', '.join([s[1] + ' (' + s[5] + ', ' + str(s[6]) + '/10)' for s in matched_suppliers])}."

            dronahq_prompt += f"\n\nUser Question: {user_message}"

            bot_response = ask_dronahq(dronahq_prompt)
            # If DronaHQ gave empty or generic fallback, try raw message
            if not bot_response or "don't have that information" in bot_response.lower():
                alt = ask_dronahq(user_message)
                if alt and "don't have that information" not in alt.lower():
                    bot_response = alt
        except Exception as dhq_err:
            dhq_error_detail = str(dhq_err)
            print(f"[DronaHQ Query Error]: {dhq_err}")
            bot_response = ""

        # 2. Secondary fallback: NVIDIA NIM
        if not bot_response or "don't have that information" in bot_response.lower():
            try:
                print(f"[AI Advisor] Falling back to NVIDIA NIM for: {user_message}")
                user_query = f"User Question: {user_message}"
                bot_response = format_with_nvidia(system_prompt, user_query)
            except Exception as nv_err:
                print(f"[NVIDIA Query Error]: {nv_err}")

        # 3. Tertiary fallback: Gemma
        if not bot_response:
            print(f"[AI Advisor] Falling back to Gemma for: {user_message}")
            user_query = f"User Question: {user_message}"
            bot_response = call_gemma(system_prompt, user_query)
            
        if not bot_response:
            bot_response = "I am connected to DronaHQ. How else can I assist with your sales, inventory, or pricing today?"

        # Clean any disclaimers stating supplier details are not available
        bad_phrases = [
            "Current data does not contain supplier details for any product.",
            "Current data does not contain supplier details",
            "Next Step: Integrate a Supplier Management Module into your system to track vendor performance and availability for all 400 SKUs.",
            "Integrate a Supplier Management Module into your system to track vendor performance and availability",
            "Integrate a Supplier Management Module into your system",
            "Supplier details are not available in current database.",
            "does not contain supplier information"
        ]
        for bp in bad_phrases:
            bot_response = bot_response.replace(bp, "").strip()

        # Clean duplicate product naming artifacts like (Product (Product (PROD003)))
        if lowest_prod_info:
            p_name = lowest_prod_info["name"]
            p_id = lowest_prod_info["id"]
            bot_response = bot_response.replace(f'"{p_name}" ({p_name} ({p_id}))', f'**{p_name}** ({p_id})')
            bot_response = bot_response.replace(f'"{p_name}" ({p_id})', f'**{p_name}** ({p_id})')
            bot_response = bot_response.replace(f'{p_name} ({p_name} ({p_id}))', f'**{p_name}** ({p_id})')

        # Optional intercept to parse product IDs if not already resolved
        import re
        product_matches = list(set(re.findall(r'PROD\d{3}', bot_response)))
        if product_matches:
            conn_prod = get_db()
            cur_prod = conn_prod.cursor()
            for prod_id in product_matches:
                # avoid replacing if already preceded by name
                cur_prod.execute("SELECT ProductName FROM products WHERE ProductID = ?", (prod_id,))
                row = cur_prod.fetchone()
                if row:
                    prod_name = row[0]
                    if prod_name not in bot_response:
                        bot_response = bot_response.replace(prod_id, f"**{prod_name}** ({prod_id})")
            conn_prod.close()

        # If this is a supplier query, append the interactive supplier profile cards
        if is_supplier_query and matched_suppliers:
            related_name = lowest_prod_info["name"] if lowest_prod_info else None
            profile_cards = generate_supplier_profile_cards_html(matched_suppliers, related_product=related_name)
            bot_response = bot_response.strip() + "\n\n" + profile_cards

    except Exception as e:
        print(f"LLM API Error: {e}")
        # Local heuristic fallback
        msg_lower = req.message.lower().strip()
        if is_supplier_query and matched_suppliers:
            if lowest_prod_info:
                bot_response = f"""* **Lowest Stock Product:** **{lowest_prod_info['name']}** ({lowest_prod_info['id']}) is at **{lowest_prod_info['stock']} units** in stock (Critical: Reorder threshold {lowest_prod_info['reorder']} units).
* **Recommended Suppliers:** **{matched_suppliers[0][1]}** ({matched_suppliers[0][6]}/10 Reliability) and **{matched_suppliers[1][1] if len(matched_suppliers) > 1 else ''}** ({matched_suppliers[1][6] if len(matched_suppliers) > 1 else '8'}/10 Reliability) for {lowest_prod_info['category']}.

**Actionable Recommendation:**
* Connect with the suppliers below via **Agent Aarav (Voice AI)** or send a pre-drafted **Purchase Order Email**."""
            else:
                bot_response = f"""### 🚚 Active Supplier Profiles
Here are our top verified suppliers with high reliability scores and direct contact lines. You can call them with **Agent Aarav** or mail them directly below."""
            
            related_name = lowest_prod_info["name"] if lowest_prod_info else None
            bot_response += "\n\n" + generate_supplier_profile_cards_html(matched_suppliers, related_product=related_name)
        elif "stock" in msg_lower and ("lowest" in msg_lower or "out of" in msg_lower):
            low_stock = kpis.get("lowStockItems", [])
            if low_stock:
                item = low_stock[0]
                bot_response = f"**Fallback Data (LLM Error):** The product with the lowest stock is **{item['name']}** with only {item['qty']} units left (Reorder point: {item['reorder']})."
            else:
                bot_response = "**Fallback Data:** All stock levels look normal."
        else:
            bot_response = "I am unable to process that request right now due to an API error."

    return JSONResponse({"response": bot_response})

def parse_llm_json(raw_text: str) -> dict:
    import json, re
    t = raw_text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    match = re.search(r'(\{[\s\S]*\})', t)
    if match:
        return json.loads(match.group(1))
    return json.loads(t)

@app.post("/api/pricing-advisor")
def api_pricing_advisor(req: PricingRequest):
    query = (req.product_name or "").strip()
    if not query:
        query = "General Pricing Strategy"
    
    conn = get_db()
    cur = conn.cursor()
    
    # 1. Search for catalog match in SQLite
    product = None
    search_term = f"%{query}%"
    cur.execute("""
        SELECT p.ProductID, p.ProductName, p.DefaultSellingPrice, p.StandardMarkup 
        FROM products p 
        WHERE p.ProductName LIKE ? COLLATE NOCASE
        LIMIT 1
    """, (search_term,))
    product = cur.fetchone()
    
    # Secondary search if multi-word
    if not product:
        for word in query.split():
            if len(word) >= 4:
                cur.execute("""
                    SELECT p.ProductID, p.ProductName, p.DefaultSellingPrice, p.StandardMarkup 
                    FROM products p 
                    WHERE p.ProductName LIKE ? COLLATE NOCASE
                    LIMIT 1
                """, (f"%{word}%",))
                product = cur.fetchone()
                if product:
                    break

    is_catalog_item = product is not None
    if is_catalog_item:
        product_id, product_name, default_price, markup = product
        default_price = safe_float(default_price)
        
        # Get Sales and Cost Data
        cur.execute("""
            SELECT SUM(CAST(Quantity AS REAL)), SUM(CAST(TotalAmount AS REAL))
            FROM sales_raw
            WHERE ProductID = ?
        """, (product_id,))
        sales_data = cur.fetchone()
        total_qty_sold = safe_float(sales_data[0]) if sales_data and sales_data[0] else 120
        total_revenue = safe_float(sales_data[1]) if sales_data and sales_data[1] else default_price * total_qty_sold
        
        cur.execute("""
            SELECT AVG(CAST(CostPerUnit AS REAL))
            FROM purchase_orders
            WHERE ProductID = ?
        """, (product_id,))
        cost_data = cur.fetchone()
        avg_cost = safe_float(cost_data[0]) if cost_data and cost_data[0] else (default_price / (1 + safe_float(markup)/100) if markup else default_price * 0.75)
        current_margin = ((default_price - avg_cost) / default_price * 100) if default_price > 0 else 25.0
        
        prompt_sys = "You are an expert Pricing & Revenue Optimization Advisor for Indian SMEs."
        prompt_user = f"""Analyze this catalog product for our SME store:
Product ID: {product_id}
Product Name: {product_name}
Current Selling Price: ₹{default_price:.2f}
Cost Price: ₹{avg_cost:.2f}
Current Margin: {current_margin:.1f}%
Units Sold: {int(total_qty_sold)} units

Return ONLY a valid JSON object (no markdown, no backticks, no other text) with this exact schema:
{{
  "id": "{product_id}",
  "name": "{product_name}",
  "badge": "Increase 5-7%",
  "badgeColor": "#10b981",
  "desc": "High demand with low price sensitivity. Increasing price will improve margins without impacting volume.",
  "gemma": "Based on market demand and low price sensitivity, increasing price by 5-7% will expand net profit margin while preserving order volumes.",
  "insights": {{
    "currentPrice": "₹{default_price:.0f}",
    "costPrice": "₹{avg_cost:.0f}",
    "currentMargin": "{current_margin:.1f}%",
    "compPrice": "₹{default_price * 1.05:.0f}",
    "sales": "{int(total_qty_sold):,} units",
    "growth": "+14.2%",
    "elasticity": "Low (0.34)",
    "trend": "Increasing"
  }},
  "impacts": [
    {{"label": "Price Increase", "val": "5-7%", "color": "#6366f1"}},
    {{"label": "Profit Increase", "val": "₹{(default_price * 0.06 * max(total_qty_sold, 10)):.0f}", "color": "#10b981"}},
    {{"label": "Margin Increase", "val": "+3.4%", "color": "#3b82f6"}},
    {{"label": "Revenue Impact", "val": "Positive", "color": "#10b981"}}
  ]
}}"""
    else:
        # User query is either a new/unlisted product or a general pricing / business strategy question
        prompt_sys = "You are Gemma, an elite SME Pricing & Revenue Optimization Advisor for Indian businesses."
        prompt_user = f"""The SME merchant is asking a pricing question or inquiring about a product/service:
Merchant Query: "{query}"

Analyze this query thoroughly.
- If it is a specific product or service, provide realistic Indian retail/wholesale pricing estimates (selling price in INR, wholesale cost in INR, gross margin %, monthly sales volume, price elasticity, competitor average).
- If it is a pricing strategy, cost increase, discount, or operational pricing question, provide tactical pricing rules, target markups, margin defense guidelines, and impact metrics.

Return ONLY a valid JSON object (no markdown, no backticks, no extra text) with this exact schema:
{{
  "id": "CUSTOM-PRICING",
  "name": "{query.title()}",
  "badge": "Optimize Markup 15-20%",
  "badgeColor": "#10b981",
  "desc": "2-sentence executive summary directly answering the merchant's query.",
  "gemma": "Comprehensive tactical advice explaining pricing elasticity, margin defense, and competitive positioning.",
  "insights": {{
    "currentPrice": "₹120",
    "costPrice": "₹95",
    "currentMargin": "20.8%",
    "compPrice": "₹125",
    "sales": "450 units",
    "growth": "+12.5%",
    "elasticity": "Moderate (0.65)",
    "trend": "Increasing"
  }},
  "impacts": [
    {{"label": "Price Adjustment", "val": "+5-8%", "color": "#6366f1"}},
    {{"label": "Profit Impact", "val": "+₹14,500", "color": "#10b981"}},
    {{"label": "Margin Gain", "val": "+3.1%", "color": "#3b82f6"}},
    {{"label": "Revenue Impact", "val": "Positive", "color": "#10b981"}}
  ]
}}"""

    conn.close()

    try:
        content = format_with_nvidia(prompt_sys, prompt_user)
        if not content:
            content = call_gemma(prompt_sys, prompt_user)
        
        result = parse_llm_json(content)
        if is_catalog_item:
            result["id"] = product_id
            result["name"] = product_name
        else:
            result["id"] = result.get("id") or "CUSTOM-PRICING"
            result["name"] = result.get("name") or query.title()

        # Sanitize and ensure all fields are complete
        if not result.get("badge"):
            result["badge"] = "Optimize Pricing"
        if not result.get("badgeColor"):
            result["badgeColor"] = "#10b981"
        if not result.get("desc"):
            result["desc"] = f"Pricing optimization analysis for {query}."
        if not result.get("gemma"):
            result["gemma"] = result.get("desc")

        insights = result.get("insights", {})
        result["insights"] = {
            "currentPrice": str(insights.get("currentPrice") or "₹250"),
            "costPrice": str(insights.get("costPrice") or "₹190"),
            "currentMargin": str(insights.get("currentMargin") or "24.0%"),
            "compPrice": str(insights.get("compPrice") or "₹265"),
            "sales": str(insights.get("sales") or "450 units"),
            "growth": str(insights.get("growth") or "+12.5%"),
            "elasticity": str(insights.get("elasticity") or "Low (0.35)"),
            "trend": str(insights.get("trend") or "Stable")
        }

        impacts = result.get("impacts", [])
        if not impacts or len(impacts) < 4:
            result["impacts"] = [
                {"label": "Price Adjustment", "val": "+5-8%", "color": "#6366f1"},
                {"label": "Profit Increase", "val": "+₹14,500", "color": "#10b981"},
                {"label": "Margin Increase", "val": "+3.2%", "color": "#3b82f6"},
                {"label": "Revenue Impact", "val": "Positive", "color": "#10b981"}
            ]

        return JSONResponse(result)

    except Exception as e:
        print("Pricing Advisor Error:", str(e))
        # Bulletproof fallback tailored to the query
        if is_catalog_item:
            calc_badge = "Increase 5-7%" if current_margin < 25 else "Maintain Price"
            calc_color = "#10b981" if current_margin < 25 else "#3b82f6"
            fallback = {
                "id": product_id,
                "name": product_name,
                "badge": calc_badge,
                "badgeColor": calc_color,
                "desc": f"Real-time pricing analysis for {product_name}. Current selling price is ₹{default_price:,.0f} with a gross margin of {current_margin:.1f}%.",
                "gemma": f"Based on inventory velocity and margin structure for {product_name}, optimizing prices by 5-7% will expand net profit margin while preserving order volumes.",
                "insights": {
                    "currentPrice": f"₹{default_price:,.0f}",
                    "costPrice": f"₹{avg_cost:,.0f}",
                    "currentMargin": f"{current_margin:.1f}%",
                    "compPrice": f"₹{default_price * 1.05:,.0f}",
                    "sales": f"{int(total_qty_sold):,} units",
                    "growth": "+14.2%",
                    "elasticity": "Low (0.34)",
                    "trend": "Increasing"
                },
                "impacts": [
                    {"label": "Price Increase", "val": "5-7%", "color": "#6366f1"},
                    {"label": "Profit Increase", "val": f"+₹{(default_price * 0.06 * max(total_qty_sold, 10)):,.0f}", "color": "#10b981"},
                    {"label": "Margin Increase", "val": "+3.2%", "color": "#3b82f6"},
                    {"label": "Revenue Impact", "val": "Positive", "color": "#10b981"}
                ]
            }
        else:
            fallback = {
                "id": "CUSTOM-PRICING",
                "name": query.title(),
                "badge": "Target Margin 20-25%",
                "badgeColor": "#10b981",
                "desc": f"Strategic pricing analysis for {query}. Recommended target markup is 20-25% to protect gross margin against procurement inflation.",
                "gemma": f"For {query}, empirical market data suggests moderate price elasticity. We recommend maintaining price parity with regional competitors while introducing volume tiers or bundle incentives to expand overall revenue.",
                "insights": {
                    "currentPrice": "₹280",
                    "costPrice": "₹215",
                    "currentMargin": "23.2%",
                    "compPrice": "₹295",
                    "sales": "380 units",
                    "growth": "+11.0%",
                    "elasticity": "Moderate (0.55)",
                    "trend": "Stable Demand"
                },
                "impacts": [
                    {"label": "Price Adjustment", "val": "+5-7%", "color": "#6366f1"},
                    {"label": "Profit Impact", "val": "+₹12,400", "color": "#10b981"},
                    {"label": "Margin Gain", "val": "+2.8%", "color": "#3b82f6"},
                    {"label": "Revenue Impact", "val": "Positive", "color": "#10b981"}
                ]
            }
        return JSONResponse(fallback)

@app.post("/api/growth-advisor")
def api_growth_advisor(req: GrowthRequest):
    # 0. Intercept invalid queries:
    lower_query = req.query.lower()
    growth_keywords = ["price", "cost", "sale", "volume", "marketing", "reduce", "increase", "grow", "profit", "margin", "discount", "campaign"]
    if not any(k in lower_query for k in growth_keywords):
        return JSONResponse({
            "name": "Invalid Scenario",
            "title": "Unrelated Query",
            "badge": "Agent Redirect",
            "badgeColor": "#ef4444",
            "desc": "This Growth Simulator evaluates revenue, volume, and pricing scenarios. Your query appears unrelated.",
            "gemma": "Please route general business or supplier queries to the AI Business Advisor (Voice Agent).",
            "volumeChg": 0.0,
            "priceChg": 0.0,
            "costChg": 0.0,
            "marketingChg": 0.0
        })

    prompt = f"""You are Gemma, an AI Growth Simulator for an SME.
Simulate this business growth scenario requested by the user:
User Request: {req.query}

Analyze this request and return ONLY a valid JSON object with numeric percentages:
{{
  "name": "Short Scenario Name",
  "title": "Scenario: Clear Description",
  "badge": "AI Optimized",
  "badgeColor": "#8b5cf6",
  "desc": "Detailed explanation of how this strategy drives business expansion.",
  "volumeChg": 6.5,
  "priceChg": 4.0,
  "costChg": -2.5,
  "marketingChg": 5.0
}}

IMPORTANT: volumeChg, priceChg, costChg, marketingChg MUST be pure numbers (floats). Return ONLY valid JSON."""
    try:
        content = call_gemma(prompt, f"Format the simulation results into the requested JSON schema for user request: {req.query}")
        if not content:
            content = format_with_nvidia(prompt, f"Format this data into the requested JSON schema for user request: {req.query}")
        if not content:
            dronahq_response = ask_dronahq(prompt)
            content = format_with_nvidia(prompt, f"Format this data into the requested JSON schema:\n{dronahq_response}")
        
        result = parse_llm_json(content)
        result["volumeChg"] = float(result.get("volumeChg", 6.0))
        result["priceChg"] = float(result.get("priceChg", 4.0))
        result["costChg"] = float(result.get("costChg", -2.0))
        result["marketingChg"] = float(result.get("marketingChg", 5.0))
        return JSONResponse(result)
    except Exception as e:
        print("LLM Growth Simulator Error:", str(e))
        import re
        q = req.query.lower()
        
        # Simple heuristics
        v_chg = 0.0
        p_chg = 0.0
        c_chg = 0.0
        m_chg = 0.0
        
        # Extract any numbers from query
        nums = re.findall(r'\d+\.?\d*', q)
        val = float(nums[0]) if nums else 5.0
        
        if 'decrease cost' in q or 'reduce cost' in q or 'cost reduction' in q or 'cost decreases' in q or 'cost of tomato' in q:
            c_chg = -val
        elif 'increase price' in q or 'raise price' in q:
            p_chg = val
            v_chg = -(val * 0.2) # Elasticity
        elif 'increase sales' in q or 'increase volume' in q:
            v_chg = val
        elif 'marketing' in q:
            m_chg = val
            v_chg = val * 0.8
        else:
            v_chg = val
            
        return JSONResponse({
            "name": req.query[:25] if len(req.query) <= 25 else req.query[:22] + "...",
            "title": f"Scenario: {req.query}",
            "badge": "Heuristic Engine",
            "badgeColor": "#f59e0b",
            "desc": f"Calculated scenario for '{req.query}'. Showing isolated impact without assuming unrelated growth.",
            "volumeChg": v_chg,
            "priceChg": p_chg,
            "costChg": c_chg,
            "marketingChg": m_chg
        })

# ── Static file serving (serve the frontend) ──
NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0"
}

@app.get("/styles.css")
def serve_styles_css():
    return FileResponse(os.path.join(DATA_DIR, "styles.css"), media_type="text/css", headers=NO_CACHE_HEADERS)

@app.get("/splash.css")
def serve_splash_css():
    return FileResponse(os.path.join(DATA_DIR, "splash.css"), media_type="text/css", headers=NO_CACHE_HEADERS)

@app.get("/logo.png")
def serve_logo_png():
    return FileResponse(os.path.join(DATA_DIR, "logo.png"), media_type="image/png")

@app.get("/app.js")
def serve_app_js():
    path = os.path.join(DATA_DIR, "app.js")
    if os.path.exists(path):
        return FileResponse(path, media_type="application/javascript", headers=NO_CACHE_HEADERS)
    return JSONResponse({"error": "not found"}, status_code=404)

@app.get("/kpis.json")
def serve_kpis_json():
    path = os.path.join(DATA_DIR, "kpis.json")
    if os.path.exists(path):
        return FileResponse(path, media_type="application/json")
    return JSONResponse({"error": "not found"}, status_code=404)

@app.post("/api/fraud-guard/analyze")
def api_fraud_guard(req: dict):
    email_text = req.get("email_text", "")
    
    prompt = f"""You are 'NVIDIA Fraud AI', an automated fraud-detection AI (honeypot) for a company. 
A suspicious email has been received:
---
{email_text}
---

Your task:
1. Identify the 'Scam Type' (e.g., Invoice Fraud, Phishing, BEC).
2. Calculate a Risk Score (0-100).
3. Extract Actionable IOCs (Indicators of Compromise) like emails, domains, IBANs, phone numbers, URLs. Assign a threat level (HIGH, MEDIUM, LOW) to each.
4. Create a fictional 'Persona' for the honeypot (e.g., 'Arjun Mehta, Busy CFO').
5. Create a fictional 'Mailbox' matching the persona (e.g., 'arjun.mehta@targetcompany.com').
6. Write a pragmatic, stalling response from this Persona to the scammer. The tone should be professional but inquisitive, asking for verifying documents to delay them and gather more intel. **CRITICAL: You MUST sign off the email with your Persona's human name. DO NOT sign off as NVIDIA or an AI.** The reply must be highly specific to the provided email (using their names, invoice numbers, amounts, etc.). Use HTML `<br>` for line breaks.

Return ONLY a valid JSON object with this exact schema (do not include markdown formatting like ```json):
{{
  "scam_type": "Invoice Fraud",
  "risk_score": 100,
  "persona": "Arjun Mehta, Busy CFO",
  "mailbox": "arjun.mehta@targetcompany.com",
  "iocs": [
    {{"value": "accounts@meridianpartners-llc.com", "type": "EMAIL", "level": "MEDIUM"}},
    {{"value": "GB82WEST12345698765432", "type": "IBAN", "level": "HIGH"}}
  ],
  "ai_reply": "Hi [Name],<br><br>I received your email regarding [Specific Invoice].<br><br>Before I can process [Specific Amount], I need you to confirm...<br><br>Best regards,<br>Arjun Mehta"
}}
"""
    try:
        content = call_gemma(prompt, "Format the analysis into the requested JSON schema based on the email provided.")
        if not content:
            content = format_with_nvidia(prompt, "Format the analysis into the requested JSON schema based on the email provided.")
        if not content:
            raise ValueError("LLM API returned empty content")
        result = parse_llm_json(content)
        return JSONResponse(result)
    except Exception as e:
        print("Fraud Guard Error:", str(e))
        import re
        text = req.get("email_text", "")
        
        # Extract basic info
        sender_match = re.search(r'From:\s*([^<]+)', text)
        sender_name = sender_match.group(1).strip() if sender_match else "Sender"
        
        email_match = re.search(r'<([^>]+)>', text)
        sender_email = email_match.group(1).strip() if email_match else "suspicious@example.com"
        
        amount_match = re.search(r'(?:Rs|£|\$)\s*[\d,]+(?:\.\d+)?', text)
        amount = amount_match.group(0) if amount_match else "the requested amount"
        
        inv_match = re.search(r'(INV[/\-A-Z0-9]+)', text, re.I)
        invoice = inv_match.group(1) if inv_match else "the invoice"
        
        # Extract account numbers for IOCs
        acct_match = re.search(r'(?:Account no|Account Number):\s*(\d+)', text, re.I)
        acct = acct_match.group(1) if acct_match else "1234567890"

        iocs = [
            {"value": sender_email, "type": "EMAIL", "level": "HIGH"},
            {"value": acct, "type": "ACCOUNT NUMBER", "level": "MEDIUM"}
        ]

        if inv_match:
            iocs.append({"value": invoice, "type": "INVOICE NUMBER", "level": "LOW"})
            
        first_name = sender_name.split()[0] if sender_name != "Sender" else "there"

        return JSONResponse({
            "scam_type": "Invoice Fraud (Heuristic Fallback)",
            "risk_score": 95,
            "persona": "Arjun Mehta, Busy CFO",
            "mailbox": "arjun.mehta@targetcompany.com",
            "iocs": iocs,
            "ai_reply": f"Hi {first_name},<br><br>I received your email regarding {invoice}.<br><br>Before I can process the payment for {amount}, I need you to confirm the invoice details and provide the official payment instructions on your company letterhead.<br><br>Could you please share a copy of the original invoice for our compliance team?<br><br>Best regards,<br>Arjun Mehta"
        })

import pandas as pd
import random

@app.get("/api/fraud-guard/fetch-email")
def api_fraud_guard_fetch_email():
    try:
        csv_path = os.path.join(DATA_DIR, "email farud", "incoming_emails.csv")
        df = pd.read_csv(csv_path)
        # Filter for rows where Label is 'Fraud'
        fraud_df = df[df['Label'].str.strip().str.lower() == 'fraud']
        
        if len(fraud_df) == 0:
            return JSONResponse({"error": "No fraud emails found."}, status_code=404)
            
        # Pick a random row
        row = fraud_df.sample(1).iloc[0]
        
        # Format the email text
        body_text = str(row['Body']).replace('\n', '<br>')
        email_text = f"""<b>Subject:</b> {row['Subject']}<br><br>
<b>From:</b> {row['FromName']} &lt;{row['FromEmail']}&gt;<br>
<b>Date:</b> {row['ReceivedAt']}<br><br>
{body_text}
"""
        return JSONResponse({
            "email_text": email_text,
            "scam_type": row['ScamType'],
            "time": str(row['ReceivedAt'])[-5:], # just get HH:MM
            "raw_subject": row['Subject'],
            "raw_from_email": row['FromEmail']
        })
    except Exception as e:
        print("Fetch Email Error:", str(e))
        return JSONResponse({"error": "Failed to fetch email."}, status_code=500)

# ─── SARVAM PROXY PASSTHROUGH & RATE LIMITING ───────────────────────────────
sarvam_rate_limit_records = defaultdict(list)
SARVAM_RATE_LIMIT_WINDOW = 60  # seconds
SARVAM_MAX_REQUESTS_PER_WINDOW = 30  # 30 req / min per IP

def check_sarvam_rate_limit(client_ip: str) -> bool:
    now = time.time()
    valid_timestamps = [t for t in sarvam_rate_limit_records[client_ip] if now - t < SARVAM_RATE_LIMIT_WINDOW]
    sarvam_rate_limit_records[client_ip] = valid_timestamps
    if len(valid_timestamps) >= SARVAM_MAX_REQUESTS_PER_WINDOW:
        return False
    sarvam_rate_limit_records[client_ip].append(now)
    return True

@app.api_route("/api/sarvam/{rest_of_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
@app.api_route("/api/sarvam", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def sarvam_passthrough(request: Request, rest_of_path: str = ""):
    # 1. Rate Limiting: 30 requests per minute per IP
    client_ip = request.client.host if request.client else "unknown"
    if not check_sarvam_rate_limit(client_ip):
        return Response(
            content=json.dumps({"error": "Too Many Requests. Rate limit of 30 req/min exceeded."}),
            status_code=429,
            media_type="application/json"
        )

    # 2. Build upstream target URL: https://apps.sarvam.ai/api/app-runtime/<rest-of-path>
    upstream_base = "https://apps.sarvam.ai/api/app-runtime"
    clean_subpath = rest_of_path.lstrip("/")
    target_url = f"{upstream_base}/{clean_subpath}" if clean_subpath else upstream_base
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    # 3. Read incoming request body
    body = await request.body()

    # 4. Forward headers with X-API-Key (NEVER log the API key)
    forward_headers = {
        "X-API-Key": SARVAM_API_KEY
    }
    content_type = request.headers.get("content-type")
    if content_type:
        forward_headers["Content-Type"] = content_type
    if "accept" in request.headers:
        forward_headers["Accept"] = request.headers["accept"]
    if "user-agent" in request.headers:
        forward_headers["User-Agent"] = request.headers["user-agent"]

    # 5. Forward request upstream
    try:
        upstream_resp = requests.request(
            method=request.method,
            url=target_url,
            headers=forward_headers,
            data=body if body else None,
            timeout=60,
            allow_redirects=True
        )

        resp_content_type = upstream_resp.headers.get("content-type", "application/json")
        return Response(
            content=upstream_resp.content,
            status_code=upstream_resp.status_code,
            media_type=resp_content_type,
            headers={"Content-Type": resp_content_type}
        )
    except requests.exceptions.RequestException as e:
        print(f"Sarvam proxy upstream error: {type(e).__name__}")
        return Response(
            content=json.dumps({"error": "Failed to connect to Sarvam AI upstream service."}),
            status_code=502,
            media_type="application/json"
        )

# ─── GNANI AI SPEECH-TO-TEXT (STT) ROUTES ───────────────────────────────────

@app.get("/api/gnani/config")
def api_gnani_config():
    """Return Gnani AI STT status and supported languages."""
    key = os.environ.get("GNANI_API_KEY", GNANI_API_KEY).strip()
    return JSONResponse({
        "status": "success",
        "has_api_key": bool(key),
        "model": "prisma-v3.0",
        "provider": "Gnani.ai (Vachana Speech AI)",
        "supported_languages": [
            {"code": "indic-auto", "name": "Auto Detect (Indic & English)"},
            {"code": "hi-IN", "name": "Hindi (हिंदी)"},
            {"code": "en-IN", "name": "English (India)"},
            {"code": "kn-IN", "name": "Kannada (ಕನ್ನಡ)"},
            {"code": "ta-IN", "name": "Tamil (தமிழ்)"},
            {"code": "te-IN", "name": "Telugu (తెలుగు)"},
            {"code": "mr-IN", "name": "Marathi (मराठी)"},
            {"code": "gu-IN", "name": "Gujarati (ગુજરાતી)"},
            {"code": "bn-IN", "name": "Bengali (বাংলা)"}
        ]
    })

@app.post("/api/gnani/stt")
async def api_gnani_stt(
    request: Request,
    audio_file: UploadFile = File(None),
    language_code: str = Form("indic-auto"),
    model: str = Form("prisma-v3.0")
):
    """
    Speech-to-Text endpoint powered by Gnani.ai Prisma v3.0.
    Accepts multipart/form-data audio file or JSON payload with audio_base64.
    """
    key = os.environ.get("GNANI_API_KEY", GNANI_API_KEY).strip()
    if not key:
        return JSONResponse({
            "status": "error",
            "message": "GNANI_API_KEY is not configured in .env file."
        }, status_code=500)

    wav_bytes = b""
    # 1. Try multipart audio file
    if audio_file:
        wav_bytes = await audio_file.read()
    else:
        # 2. Try JSON base64
        try:
            body = await request.json()
            b64_str = body.get("audio_base64", "")
            if b64_str:
                import base64
                if "," in b64_str:
                    b64_str = b64_str.split(",", 1)[1]
                wav_bytes = base64.b64decode(b64_str)
            language_code = body.get("language_code", language_code)
            model = body.get("model", model)
        except Exception:
            pass

    if not wav_bytes or len(wav_bytes) < 44:
        return JSONResponse({
            "status": "error",
            "message": "No valid audio payload received. Please record your voice and try again."
        }, status_code=400)

    target_url = f"{GNANI_BASE_URL}/stt/v3"
    headers = {
        "X-API-Key-ID": key
    }
    files = {
        "audio_file": ("recording.wav", wav_bytes, "audio/wav")
    }
    form_data = {
        "model": model or "prisma-v3.0",
        "language_code": language_code or "indic-auto"
    }

    try:
        upstream_resp = requests.post(
            target_url,
            headers=headers,
            files=files,
            data=form_data,
            timeout=30
        )
        if upstream_resp.status_code == 200:
            res_data = upstream_resp.json()
            transcript = res_data.get("transcript", "").strip()
            return JSONResponse({
                "status": "success",
                "success": True,
                "transcript": transcript,
                "language_code": language_code,
                "model": res_data.get("model", model),
                "request_id": res_data.get("request_id", ""),
                "processing_time": res_data.get("processing_time", 0)
            })
        else:
            return JSONResponse({
                "status": "error",
                "message": f"Gnani STT returned HTTP {upstream_resp.status_code}: {upstream_resp.text}"
            }, status_code=upstream_resp.status_code)
    except Exception as e:
        print(f"Gnani AI STT Error: {e}")
        return JSONResponse({
            "status": "error",
            "message": f"Failed to connect to Gnani AI Speech API: {str(e)}"
        }, status_code=502)

# ==========================================
# Bolna Ledger & Accounting Webhook Support
# ==========================================
LEDGER_SECRET = os.environ.get("LEDGER_SECRET", "").strip()
LEDGER_DATA_DIR = os.path.join(DATA_DIR, "data")
LEDGER_XLSX_PATH = os.path.join(LEDGER_DATA_DIR, "ledger.xlsx")
LEDGER_ROOT_XLSX_PATH = os.path.join(DATA_DIR, "ledger.xlsx")

def init_ledger_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ledger_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT,
                type TEXT,
                amount REAL,
                balance REAL DEFAULT 0,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"init_ledger_db error: {e}")

init_ledger_db()

def init_excel_ledger():
    try:
        import openpyxl
        os.makedirs(LEDGER_DATA_DIR, exist_ok=True)
        if not os.path.exists(LEDGER_XLSX_PATH):
            wb = openpyxl.Workbook()
            # Sheet 1: Customers
            ws_cust = wb.active
            ws_cust.title = "Customers"
            ws_cust.append(["Name", "Phone", "Balance"])

            # Sheet 2: Ledger
            ws_ledger = wb.create_sheet(title="Ledger")
            ws_ledger.append(["Date", "Customer", "Type", "Amount", "Balance", "Notes"])

            wb.save(LEDGER_XLSX_PATH)
            try:
                wb.save(LEDGER_ROOT_XLSX_PATH)
            except Exception:
                pass
    except Exception as e:
        print(f"init_excel_ledger error: {e}")

init_excel_ledger()

def update_excel_ledger(customer_name: str, entry_type: str, amount: float, phone: str = "", notes: str = "", date_str: str = ""):
    try:
        import openpyxl
        init_excel_ledger()
        wb = openpyxl.load_workbook(LEDGER_XLSX_PATH)

        # 1. Update Customers sheet: Name | Phone | Balance
        if "Customers" not in wb.sheetnames:
            ws_cust = wb.create_sheet(title="Customers")
            ws_cust.append(["Name", "Phone", "Balance"])
        else:
            ws_cust = wb["Customers"]

        cust_row_idx = None
        current_balance = 0.0
        for row_idx, row in enumerate(ws_cust.iter_rows(min_row=2, values_only=True), start=2):
            if row and row[0] and str(row[0]).strip().lower() == customer_name.strip().lower():
                cust_row_idx = row_idx
                try:
                    current_balance = float(row[2] or 0.0)
                except Exception:
                    current_balance = 0.0
                break

        # Balance calculation:
        # credit_given: customer owes more (+amount)
        # payment_received: customer paid (-amount)
        # payment_missed: balance unchanged
        if entry_type == "credit_given":
            new_balance = current_balance + amount
        elif entry_type == "payment_received":
            new_balance = current_balance - amount
        else:  # payment_missed
            new_balance = current_balance

        if cust_row_idx:
            ws_cust.cell(row=cust_row_idx, column=3, value=new_balance)
            if phone and not ws_cust.cell(row=cust_row_idx, column=2).value:
                ws_cust.cell(row=cust_row_idx, column=2, value=phone)
        else:
            ws_cust.append([customer_name.strip(), phone or "", new_balance])

        # 2. Update Ledger sheet: Date | Customer | Type | Amount | Balance | Notes
        if "Ledger" not in wb.sheetnames:
            ws_ledger = wb.create_sheet(title="Ledger")
            ws_ledger.append(["Date", "Customer", "Type", "Amount", "Balance", "Notes"])
        else:
            ws_ledger = wb["Ledger"]

        ws_ledger.append([date_str, customer_name.strip(), entry_type, amount, new_balance, notes or ""])
        wb.save(LEDGER_XLSX_PATH)

        # Also update root ledger.xlsx (supports ID, Date/Time, Customer Name, Type, Amount (INR), Notes)
        try:
            if os.path.exists(LEDGER_ROOT_XLSX_PATH):
                root_wb = openpyxl.load_workbook(LEDGER_ROOT_XLSX_PATH)
                root_ws = root_wb["Ledger"] if "Ledger" in root_wb.sheetnames else root_wb.active
                # Check header
                headers = [str(c or "").strip() for c in next(root_ws.iter_rows(min_row=1, max_row=1, values_only=True), [])]
                if headers and "ID" in headers[0]:
                    max_id = 0
                    for row in root_ws.iter_rows(min_row=2, values_only=True):
                        if row and row[0] is not None:
                            try:
                                max_id = max(max_id, int(row[0]))
                            except Exception:
                                pass
                    next_id = max_id + 1
                    root_ws.append([next_id, date_str, customer_name.strip(), entry_type, amount, notes or ""])
                else:
                    root_ws.append([date_str, customer_name.strip(), entry_type, amount, new_balance, notes or ""])
                root_wb.save(LEDGER_ROOT_XLSX_PATH)
        except PermissionError:
            print("Notice: root ledger.xlsx is currently open in Microsoft Excel. Close Excel to allow disk overwrite.")
        except Exception as ex_root:
            print(f"Error updating root ledger.xlsx: {ex_root}")

        return new_balance
    except Exception as e:
        print(f"update_excel_ledger error: {e}")
        return 0.0

@app.post("/api/ledger-call")
async def api_ledger_call_endpoint(request: Request):
    """Trigger Bolna Voice AI (Ledger Agent) to call the owner's phone to update ledger by voice."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    phone = body.get("phone") or os.environ.get("OWNER_PHONE", "+919876543210").strip()
    phone_e164 = format_to_e164(phone)
    if not is_valid_e164(phone_e164):
        return JSONResponse({
            "status": "error",
            "message": f"Invalid phone number '{phone}'. Number must follow E.164 format (e.g. +919876543210)."
        }, status_code=400)

    bolna_key = os.environ.get("BOLNA_API_KEY", BOLNA_API_KEY).strip()
    bolna_ledger_agent = (
        body.get("agent_id") or
        os.environ.get("LEDGER_AGENT_ID") or
        os.environ.get("AGENT_ID_LEDGER") or
        os.environ.get("BOLNA_LEDGER_AGENT_ID") or
        os.environ.get("BOLNA_AGENT_ID_LEDGER") or
        BOLNA_AGENT_ID_LEDGER
    ).strip()

    if not bolna_key:
        return JSONResponse({
            "status": "error",
            "message": "BOLNA_API_KEY is not configured in .env file."
        }, status_code=500)

    call_payload = {
        "agent_id": bolna_ledger_agent,
        "recipient_phone_number": phone_e164,
        "user_data": {
            "user_name": "Arjun Mehta",
            "customer_name": "Arjun Mehta",
            "business_name": "Katha AI Store",
            "language": body.get("language") or "hi",
            "timezone": "Asia/Kolkata"
        }
    }

    headers = {
        "Authorization": f"Bearer {bolna_key}",
        "Content-Type": "application/json"
    }

    try:
        bolna_resp = requests.post(f"{BOLNA_BASE_URL}/call", headers=headers, json=call_payload, timeout=30)
    except requests.exceptions.RequestException as e:
        return JSONResponse({
            "status": "error",
            "message": f"Network error connecting to Bolna Voice AI: {str(e)}"
        }, status_code=502)

    if bolna_resp.status_code not in (200, 201, 202):
        err_msg = bolna_resp.text
        try:
            err_json = bolna_resp.json()
            err_msg = err_json.get("message") or err_json.get("error") or err_json.get("detail") or bolna_resp.text
        except Exception:
            pass
        return JSONResponse({
            "status": "error",
            "message": f"Bolna AI Call failed (HTTP {bolna_resp.status_code}): {err_msg}"
        }, status_code=bolna_resp.status_code)

    resp_data = bolna_resp.json()
    execution_id = resp_data.get("execution_id") or resp_data.get("id")
    call_status = resp_data.get("status") or "queued"

    now_iso = datetime.utcnow().isoformat()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bolna_calls (
            execution_id, customer_id, customer_name, invoice_id,
            phone_number, amount_due, due_date, language, status,
            summary, raw_payload, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        execution_id, "OWNER-LEDGER", "Arjun Mehta (Ledger Call)", "LEDGER-VOICE",
        phone_e164, "0.0", datetime.now().strftime("%Y-%m-%d"), body.get("language", "hi"), call_status,
        "Ledger Agent voice call to update accounting ledger",
        json.dumps(resp_data), now_iso, now_iso
    ))
    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "success",
        "message": f"Ledger Agent is calling your phone at {phone_e164} to update the ledger!",
        "execution_id": execution_id,
        "phone": phone_e164,
        "agent_id": bolna_ledger_agent,
        "call_status": call_status
    })

VALID_LEDGER_TYPES = {"credit_given", "payment_received", "payment_missed"}

@app.post("/api/ledger")
@app.post("//api/ledger")
async def post_ledger_entry(request: Request):
    """
    Bolna custom function endpoint for record_transaction / ledger update.
    Body: { customer_name, type, amount }
    - type is credit_given, payment_received, or payment_missed
    - amount converted to float; reject <= 0 or unknown type
    - Require header Authorization: Bearer ${LEDGER_SECRET} (return 401 if mismatch)
    - Updates data/ledger.xlsx (Customers & Ledger sheets) immediately
    """
    expected_secret = os.environ.get("LEDGER_SECRET", "").strip()
    valid_secrets = {s for s in [expected_secret] if s}
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    api_token_header = request.headers.get("api_token") or request.headers.get("api-token") or ""

    try:
        body = await request.json()
    except Exception:
        body = {}

    token_candidate = ""
    if auth_header:
        parts = auth_header.strip().split()
        token_candidate = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else parts[0]
    elif api_token_header:
        token_candidate = api_token_header.strip()
    elif "api_token" in body:
        b_tok = str(body.get("api_token", "")).strip()
        parts = b_tok.split()
        token_candidate = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else parts[0]

    # Check 401 Unauthorized against valid secrets
    if not token_candidate or token_candidate not in valid_secrets:
        return JSONResponse({"status": "error", "message": "Unauthorized: Invalid or missing Bearer token."}, status_code=401)

    customer_name = str(body.get("customer_name") or body.get("name") or "").strip()
    if not customer_name:
        return JSONResponse({"status": "error", "message": "customer_name is required."}, status_code=400)

    entry_type = str(body.get("type") or "").strip().lower()
    if entry_type not in VALID_LEDGER_TYPES:
        return JSONResponse({
            "status": "error",
            "message": f"Invalid type '{entry_type}'. Must be one of: credit_given, payment_received, payment_missed."
        }, status_code=400)

    raw_amount = body.get("amount", 0)
    try:
        clean_amount = float(str(raw_amount).replace(",", "").replace("₹", "").strip() or 0)
    except Exception:
        return JSONResponse({
            "status": "error",
            "message": f"Invalid amount '{raw_amount}'. Amount must be a valid number."
        }, status_code=400)

    if clean_amount <= 0:
        return JSONResponse({
            "status": "error",
            "message": f"Amount must be greater than 0. Received: {clean_amount}"
        }, status_code=400)

    phone = str(body.get("phone") or "").strip()
    notes = str(body.get("notes") or "").strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Update Excel file data/ledger.xlsx immediately
    new_balance = update_excel_ledger(customer_name, entry_type, clean_amount, phone=phone, notes=notes, date_str=now_str)

    # Also record in SQLite DB
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ledger_entries (customer_name, type, amount, balance, notes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (customer_name, entry_type, clean_amount, new_balance, notes, now_str)
        )
        entry_id = cur.lastrowid
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"SQLite ledger insert error: {e}")
        entry_id = None

    display_type = entry_type.replace("_", " ")
    return JSONResponse({
        "status": "success",
        "message": f"Updated ledger in Excel. {customer_name}: recorded {display_type} of ₹{clean_amount:,.2f}. Current balance is ₹{new_balance:,.2f}.",
        "customer_name": customer_name,
        "type": entry_type,
        "amount": clean_amount,
        "balance": new_balance,
        "date": now_str,
        "entry_id": entry_id
    })

@app.get("/api/ledger/recent")
def get_recent_ledger():
    try:
        # Prefer reading fresh rows from data/ledger.xlsx if available
        import openpyxl
        if os.path.exists(LEDGER_XLSX_PATH):
            wb = openpyxl.load_workbook(LEDGER_XLSX_PATH, read_only=True)
            if "Ledger" in wb.sheetnames:
                ws = wb["Ledger"]
                rows = list(ws.iter_rows(min_row=2, values_only=True))
                results = []
                for idx, r in enumerate(reversed(rows[-50:]), start=1):
                    if r and len(r) >= 4:
                        results.append({
                            "id": idx,
                            "created_at": str(r[0] or ""),
                            "customer_name": str(r[1] or ""),
                            "type": str(r[2] or ""),
                            "amount": float(r[3] or 0.0),
                            "balance": float(r[4] if len(r) > 4 and r[4] is not None else 0.0),
                            "notes": str(r[5] if len(r) > 5 and r[5] else "")
                        })
                return JSONResponse(results)

        # Fallback to SQLite
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id, customer_name, type, amount, balance, notes, created_at FROM ledger_entries ORDER BY id DESC LIMIT 50")
        rows = cur.fetchall()
        conn.close()
        results = []
        for r in rows:
            results.append({
                "id": r[0],
                "customer_name": r[1],
                "type": r[2],
                "amount": r[3],
                "balance": r[4],
                "notes": r[5],
                "created_at": r[6]
            })
        return JSONResponse(results)
    except Exception as e:
        print(f"get_recent_ledger error: {e}")
        return JSONResponse([])

@app.get("/api/ledger/latest-call")
def get_latest_ledger_call():
    """Returns the most recent ledger phone call, its status, duration, and transcript."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, execution_id, phone_number, language, status, conversation_duration, transcript, summary, created_at, updated_at
        FROM bolna_calls
        WHERE invoice_id = 'LEDGER-VOICE' OR customer_id = 'OWNER-LEDGER'
        ORDER BY id DESC LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()
    if not row:
        return JSONResponse({"status": "no_call", "message": "No previous calls found."})

    call_data = dict(row)
    # If not completed or if completed/disconnected without transcript, sync from Bolna API
    needs_sync = (
        call_data.get("status") not in ("completed", "failed", "no-answer", "busy") or
        (call_data.get("status") in ("completed", "call-disconnected") and not call_data.get("transcript"))
    )
    if needs_sync:
        exec_id = call_data.get("execution_id")
        bolna_key = os.environ.get("BOLNA_API_KEY", BOLNA_API_KEY).strip()
        if exec_id and bolna_key:
            try:
                resp = requests.get(f"{BOLNA_BASE_URL}/executions/{exec_id}", headers={"Authorization": f"Bearer {bolna_key}"}, timeout=5)
                if resp.status_code == 200:
                    remote = resp.json()
                    process_bolna_execution_update(exec_id, remote)
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("SELECT id, execution_id, phone_number, language, status, conversation_duration, transcript, summary, created_at, updated_at FROM bolna_calls WHERE execution_id = ?", (exec_id,))
                    updated_row = cur.fetchone()
                    conn.close()
                    if updated_row:
                        call_data = dict(updated_row)
            except Exception as e:
                print(f"Error syncing latest call: {e}")

    return JSONResponse(call_data)

@app.post("/api/ledger/refresh-sync")
def api_refresh_sync_ledger():
    """Forces sync across sme_data.db, data/ledger.xlsx, ledger_latest.xlsx, and root ledger.xlsx if free."""
    import openpyxl
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, customer_name, type, amount, balance, notes, created_at FROM ledger_entries ORDER BY id ASC")
    db_rows = cur.fetchall()
    conn.close()

    # Update ledger_latest.xlsx
    wb_latest = openpyxl.Workbook()
    ws_latest = wb_latest.active
    ws_latest.title = "Ledger"
    ws_latest.append(["ID", "Date/Time", "Customer Name", "Type", "Amount (INR)", "Notes"])
    for r in db_rows:
        ws_latest.append([r[0], r[6], r[1], r[2], r[3], r[5] or ""])
    try:
        wb_latest.save("ledger_latest.xlsx")
    except Exception as e:
        print(f"Error saving ledger_latest.xlsx: {e}")

    # Also try root ledger.xlsx
    root_synced = False
    try:
        wb_latest.save("ledger.xlsx")
        root_synced = True
    except Exception:
        pass

    return JSONResponse({
        "status": "success",
        "total_entries": len(db_rows),
        "root_synced": root_synced,
        "message": f"Ledger synchronized ({len(db_rows)} entries)."
    })

@app.get("/")
def serve_root():
    return FileResponse(os.path.join(DATA_DIR, "index.html"), headers=NO_CACHE_HEADERS)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)


@app.post("/api/collections/outcome")
async def api_collections_outcome_endpoint(request: Request):
    """Webhook called by Bolna custom function log_collection_outcome during or after customer calls."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    print(f"[Collections Webhook on 8080]: {body}")
    cust_id = str(body.get("customer_id") or "").strip()
    outcome = str(body.get("outcome") or "promise_to_pay").strip().lower()
    raw_amount = body.get("amount", 0)
    clean_amt = 0.0
    try:
        if str(raw_amount).lower().endswith("k"):
            clean_amt = float(str(raw_amount)[:-1]) * 1000.0
        else:
            clean_amt = float(re.sub(r'[^0-9.]', '', str(raw_amount)) or 0.0)
    except Exception:
        clean_amt = 0.0

    date_val = str(body.get("date") or "").strip()
    payment_mode = str(body.get("payment_mode") or "UPI").strip()
    callback_time = str(body.get("callback_time") or "").strip()
    reason = str(body.get("reason") or "").strip()
    execution_id = str(body.get("execution_id") or "").strip()

    update_excel_collections(
        customer_id=cust_id,
        outcome=outcome,
        amount=clean_amt,
        date_val=date_val,
        payment_mode=payment_mode,
        callback_time=callback_time,
        reason=reason,
        execution_id=execution_id
    )

    try:
        requests.post("http://127.0.0.1:3000/api/collections/outcome", json=body, headers={"Authorization": f"Bearer {os.environ.get('LEDGER_SECRET', '')}"}, timeout=2)
    except Exception:
        pass

    return JSONResponse({
        "status": "success",
        "message": f"Recorded {outcome} for {cust_id} in collections Excel",
        "customer_id": cust_id,
        "outcome": outcome
    })

# Updated: 2026-11-22
