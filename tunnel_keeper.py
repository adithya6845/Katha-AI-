import subprocess, re, time, requests, json, sys, os, threading
from dotenv import load_dotenv

load_dotenv(".env", override=True)

CURRENT_DOMAIN = None

def update_voice_agent_url(tunnel_domain):
    global CURRENT_DOMAIN
    load_dotenv(".env", override=True)
    voice_api_key = os.environ.get("VOICE_API_KEY") or os.environ.get("BOLNA_API_KEY", "")
    agent_id_ledger = os.environ.get("LEDGER_AGENT_ID") or os.environ.get("AGENT_ID_LEDGER") or os.environ.get("BOLNA_AGENT_ID", "")
    agent_id_collections = os.environ.get("COLLECTIONS_AGENT_ID") or os.environ.get("AGENT_ID_PAYMENT") or os.environ.get("BOLNA_COLLECTIONS_AGENT_ID", "")
    ledger_secret = os.environ.get("LEDGER_SECRET", "")

    domain = tunnel_domain.strip().rstrip('/')
    if not domain.startswith('http'):
        domain = f"https://{domain}"
    while 'https://https://' in domain:
        domain = domain.replace('https://https://', 'https://')

    CURRENT_DOMAIN = domain
    headers = {
        "Authorization": f"Bearer {voice_api_key}",
        "Content-Type": "application/json"
    }

    # 1. Update Ledger Agent
    try:
        webhook_url = f"{domain}/api/ledger"
        r = requests.get(f"https://api.bolna.ai/v2/agent/{agent_id_ledger}", headers=headers, timeout=10)
        if r.status_code == 200:
            agent_data = r.json()
            task0 = agent_data.get("tasks", [])[0]
            api_tools = task0.get("tools_config", {}).get("api_tools", {})
            if "tools_params" in api_tools and "record_transaction" in api_tools["tools_params"]:
                api_tools["tools_params"]["record_transaction"]["url"] = webhook_url
                api_tools["tools_params"]["record_transaction"]["api_token"] = f"Bearer {ledger_secret}"
                requests.put(
                    f"https://api.bolna.ai/v2/agent/{agent_id_ledger}",
                    headers=headers,
                    json={"agent_config": agent_data},
                    timeout=10
                )
                print(f"[+] Synced Ledger Agent webhook to: {webhook_url}")
    except Exception as e:
        print(f"[!] Error updating Ledger agent: {e}")

    # 2. Update Collections Agent (Meera)
    try:
        col_webhook_url = f"{domain}/api/collections/outcome"
        r2 = requests.get(f"https://api.bolna.ai/v2/agent/{agent_id_collections}", headers=headers, timeout=10)
        if r2.status_code == 200:
            col_agent_data = r2.json()
            task0 = col_agent_data.get("tasks", [])[0]
            api_tools = task0.get("tools_config", {}).get("api_tools", {})
            if "tools_params" in api_tools and "log_collection_outcome" in api_tools["tools_params"]:
                api_tools["tools_params"]["log_collection_outcome"]["url"] = col_webhook_url
                api_tools["tools_params"]["log_collection_outcome"]["api_token"] = f"Bearer {ledger_secret}"
                requests.put(
                    f"https://api.bolna.ai/v2/agent/{agent_id_collections}",
                    headers=headers,
                    json={"agent_config": col_agent_data},
                    timeout=10
                )
                print(f"[+] Synced Collections Agent webhook to: {col_webhook_url}")
    except Exception as e:
        print(f"[!] Error updating Collections agent: {e}")

def periodic_sync_worker():
    while True:
        try:
            if CURRENT_DOMAIN:
                update_voice_agent_url(CURRENT_DOMAIN)
        except Exception as e:
            pass
        time.sleep(45)

def run_tunnel():
    global CURRENT_DOMAIN
    cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-R", "80:127.0.0.1:8080",
        "nokey@localhost.run"
    ]
    print("[*] Launching localhost.run tunnel...")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    domain = None
    for line in iter(proc.stdout.readline, ''):
        sys.stdout.write(line)
        sys.stdout.flush()
        match = re.search(r'(https://[a-zA-Z0-9]+\.app\.lhr\.life)', line)
        if match and match.group(1) != domain:
            domain = match.group(1)
            print(f"\n[>>>] Tunnel Live: {domain}")
            update_voice_agent_url(domain)

    proc.stdout.close()
    proc.wait()
    print("[!] Tunnel process exited. Reconnecting in 3 seconds...")

def main():
    sync_thread = threading.Thread(target=periodic_sync_worker, daemon=True)
    sync_thread.start()

    while True:
        try:
            run_tunnel()
        except Exception as e:
            print(f"[!] Tunnel exception: {e}")
        time.sleep(3)

if __name__ == "__main__":
    main()

# Updated: 2026-11-22
