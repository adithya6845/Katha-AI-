# Katha AI — Autonomous Voice AI & Smart Hardware Operating System for SMEs

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![Node.js 18+](https://img.shields.io/badge/Node.js-18%2B-green.svg)](https://nodejs.org/)
[![Docker Ready](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](Dockerfile)
[![Render Cloud](https://img.shields.io/badge/Deploy-Render-46E3B7.svg)](render.yaml)
[![Hardware](https://img.shields.io/badge/Hardware-Raspberry%20Pi%20%2B%20TFT-c51a4a.svg)](hardware/README.md)

**Katha AI** is a complete, full-stack business operating system purpose-built for Indian retail and distribution SMEs. Combining a **physical Raspberry Pi counter terminal with TFT display & smart speaker**, a suite of **specialized Voice AI agents** (Hindi, Hinglish, English), and a **real-time bi-directional Microsoft Excel synchronization engine**, Katha AI automates collections recovery, cashflow forecasting, supplier procurement, and ledger bookkeeping.

---

## 🌟 Ecosystem Overview

```
                                      ┌─────────────────────────────────────────────────────────┐
                                      │             Katha AI Smart Counter Terminal             │
                                      │                                                         │
                                      │   ┌────────────────────┐       ┌────────────────────┐   │
                                      │   │  TFT Touchscreen   │       │  Speaker & Mic     │   │
                                      │   │ (Counter Display)  │       │ (Voice Interaction)│   │
                                      │   └─────────▲──────────┘       └─────────▲──────────┘   │
                                      │             │                            │              │
                                      │             └─────────────┬──────────────┘              │
                                      │                           │                             │
                                      │                ┌──────────┴──────────┐                  │
                                      │                │   Raspberry Pi 4/5  │                  │
                                      │                │  (Chromium Kiosk)   │                  │
                                      │                └──────────┬──────────┘                  │
                                      └───────────────────────────┼─────────────────────────────┘
                                                                  │ HTTPS / WebSockets
                                                                  ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       Katha AI Cloud Platform (Render / Local)                                    │
│                                                                                                                   │
│   ┌──────────────────────────────────────────────┐              ┌──────────────────────────────────────────────┐  │
│   │             FastAPI Backend (Port 8080)      │              │           Node.js Gateway (Port 3000)        │  │
│   │  • Voice AI Gateway (Meera, Kavya, Aarav)   │◄────────────►│  • Live Collections & Webhook Dispatch       │  │
│   │  • Gnani AI Speech-to-Text Engine            │ Proxy Stream │  • ExcelJS Sequential Write Queue            │  │
│   │  • DronaHQ, Gemma & NVIDIA NIM Copilot       │              │  • Ledger Sync & Payment Verification        │  │
│   └──────────────────────┬───────────────────────┘              └──────────────────────┬───────────────────────┘  │
└──────────────────────────┼─────────────────────────────────────────────────────────────┼──────────────────────────┘
                           │                                                             │
                           ▼                                                             ▼
         ┌───────────────────────────────────┐                         ┌───────────────────────────────────┐
         │       Voice Telecom Gateway       │                         │       Real-Time Excel Engine      │
         │  • Outbound Customer Calls (Meera)│                         │  • data/collections.xlsx          │
         │  • Supplier Negotiations (Aarav)  │                         │  • data/ledger.xlsx               │
         │  • Owner Daily Briefing (Kavya)   │                         │  • Automatic Atomic Backups       │
         │  • Ledger Bookkeeper (Priya)      │                         │  • Strict In-Memory Write Queue   │
         └───────────────────────────────────┘                         └───────────────────────────────────┘
```

---

## 🖥️ Smart Hardware Counter Terminal (Raspberry Pi + TFT + Speaker)

Katha AI transforms standard hardware into an enterprise-grade counter device for retail counters:

- **Single-Board Computer:** Raspberry Pi 4 Model B / Raspberry Pi 5.
- **Display:** 3.5", 5", or 7" HDMI/SPI TFT Touchscreen running full-screen Chromium Kiosk mode.
- **Audio Output:** Integrated USB or 3.5mm AUX Speaker for voice agent dialogue.
- **Audio Input:** USB Gooseneck Mic or ReSpeaker Pi HAT capturing commands for Gnani STT.
- **Automated Startup:** Complete `kiosk_boot.sh` script with screen power management (`xset -dpms`), cursor hiding (`unclutter`), volume balancing (`amixer`), and `systemd` service autostart.

👉 **Complete Hardware BOM, wiring guide, and configuration:** [hardware/README.md](hardware/README.md)

---

## 🤖 Specialized Voice AI Agent Suite

| Agent | Avatar / Name | Primary Mission | Languages | Channel |
|---|---|---|---|---|
| **Collections Recovery** | 🎧 **Meera** | Calls customers with overdue udhaar, politely negotiates payment timelines, records promised payment amounts & dates directly into Excel. | Hindi, Hinglish, English | Outbound Telephony |
| **Executive Business Advisor** | 💼 **Kavya** | Briefs shop owner on daily gross revenue, net profit margin, overdue udhaar risk, and critical inventory reorder items. | Hindi, English | Phone Call & Interactive Chat |
| **Supplier Procurement** | 🚚 **Aarav** | Calls vendors to negotiate bulk wholesale discounts, verifies SKU stock availability, and drafts purchase orders. | English, Hindi | Outbound Telephony & Email |
| **Voice Bookkeeper** | 📒 **Priya** | Takes voice dictation from the owner ("Received ₹5,000 cash from Ramesh") and logs balanced double-entry accounting transactions. | Hindi, Hinglish | Inbound / Outbound Voice |
| **Multilingual STT** | 🎙️ **Gnani AI** | High-accuracy speech-to-text transcription optimized for regional Indian business vernacular and dialects. | Indian Regional | Mic Stream |

---

## 📊 Dual-Direction Real-Time Excel Engine

The core data backbone is managed directly in Microsoft Excel (`.xlsx`), enabling business owners to use familiar spreadsheet tools while the AI operates seamlessly:

### 1. `data/collections.xlsx`
- **`Receivables`**: Tracks 250+ accounts receivable with customer details, invoice numbers, amounts, overdue days, risk tiers, and promise-to-pay statuses (`Promise made`, `Payment claimed`, `Disputed`).
- **`CollectionsLog`**: Complete audit log of every phone call, timestamp, duration, recording link, promised amount, and outcome.
- **`Payments`**: Immutable record of confirmed payments verified by the owner.

### 2. `data/ledger.xlsx`
- **`Journal`**: Standard double-entry bookkeeping journal entries with date, account, debit, credit, and narration.
- **`Transactions`**: Real-time sales, receipts, expenses, and supplier payouts.
- **`AccountBalances`**: Dynamic balances for Cash, Bank, Receivables, Sales, and Expense accounts.

> [!TIP]
> **Data Integrity Guarantee:** All Excel operations utilize a sequential Promise queue (`runCollectionsExclusive`) to eliminate race conditions, file locks, and corruption during concurrent voice call updates.

---

## 🚀 Quick Start Guide

### Prerequisites
- Python 3.11+
- Node.js 18+
- Git

### 1. Clone & Configure
```bash
git clone https://github.com/adithya6845/Katha-AI-.git
cd Katha-AI-
cp .env.example .env
```
Fill in your credentials in `.env`:
```env
VOICE_API_KEY=your_voice_api_key
COLLECTIONS_AGENT_ID=your_collections_agent_id
AGENT_ID_ADVISOR=your_advisor_agent_id
AGENT_ID_SUPPLIER=your_supplier_agent_id
LEDGER_AGENT_ID=your_ledger_agent_id
DEFAULT_CUSTOMER_PHONE=+916363077110
DEFAULT_SUPPLIER_PHONE=+916363077110
LEDGER_SECRET=your_secret_token
```

### 2. Run Locally
**Terminal 1 (Python FastAPI Backend):**
```bash
pip install -r requirements.txt
python api.py
# Runs on http://localhost:8080
```

**Terminal 2 (Node.js Gateway & Excel Engine):**
```bash
npm install
node server.js
# Runs on http://localhost:3000
```

Open your browser to `http://localhost:3000` to access the Katha AI dashboard.

---

## ☁️ 1-Click Render Cloud Deployment

Deploy Katha AI with Docker on Render:

1. Connect your repository to **[Render](https://render.com)**.
2. Select **Web Service** using the included `Dockerfile` and `render.yaml`.
3. Set your environment variables in the Render Dashboard (`VOICE_API_KEY`, `AGENT_ID_ADVISOR`, `COLLECTIONS_AGENT_ID`, `LEDGER_AGENT_ID`, `DEFAULT_CUSTOMER_PHONE`, etc.).
4. The service will automatically run `start.sh`, launching both Python FastAPI and Node.js on port `$PORT`.

---

## 📱 Hardware Kiosk Setup (Raspberry Pi)

1. Flash **Raspberry Pi OS with Desktop** onto a MicroSD card.
2. Clone this repository on the Pi:
   ```bash
   git clone https://github.com/adithya6845/Katha-AI-.git ~/Katha-AI-
   ```
3. Run the hardware kiosk installer:
   ```bash
   cd ~/Katha-AI-/hardware
   chmod +x kiosk_boot.sh
   sudo cp katha-kiosk.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now katha-kiosk.service
   ```
On reboot, the Raspberry Pi will automatically launch full-screen into the Katha AI counter terminal.

---

## 🔒 Security & Privacy

- **Strict Credential Isolation:** `.env`, `.pem`, and local configuration files are excluded via `.gitignore` and `.dockerignore`.
- **Zero Hardcoded Secrets:** All outbound telephony, AI APIs, and webhook authentications require explicit environment variables.
- **Bearer Token Webhook Verification:** All automated webhook endpoints validate incoming calls via `LEDGER_SECRET`.

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
