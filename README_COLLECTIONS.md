# Collections Agent & Real-Time Excel Integration (README)

This module integrates the existing **Collections Agent** screen with **Voice AI (Agent Meera)** and the Excel tracking system (`data/collections.xlsx`).

---

## 1. Files Changed & Preserved

| File | Status | Description |
|---|---|---|
| `data/collections.xlsx` | Created / Managed | Seeded with 250 customer records from the database across 3 sheets (`Receivables`, `CollectionsLog`, `Payments`). |
| `server.js` | Updated | Implemented complete Node.js + Express + `exceljs` backend for `/api/collections/*`, write queue, pre-write backups, and post-call watcher. |
| `api.py` | Updated | Added transparent proxy for `/api/collections/{path}` forwarding to Node.js backend. |
| `tunnel_keeper.py` | Updated | Auto-syncs live webhook URL for both Ledger Agent and Collections Agent (Meera). |
| `app.js` | Updated | Connected existing Collections modal buttons ("Call with Meera", "Email Reminder", "Dismiss", "Verify payment", "Reject claim") and dynamic rule-based AI recommendations with **ZERO changes to visual design, layout, fonts, or colors**. |
| `.env` | Updated | Added `COLLECTIONS_AGENT_ID=your_collections_agent_id` and `TEST_MODE=true`. |
| `.env.example` | Created | Template for environment variables. |

---

## 2. Safety Rules & Architecture

1. **Rule: Claims vs Verification**:
   - When a customer says "I already paid" during a call, Agent Meera records `payment_claimed`.
   - **`AmountPending` is NOT reduced automatically**.
   - The UI displays **"Verify payment"** and **"Reject claim"** buttons. Only pressing "Verify payment" reduces `AmountPending` and logs a row in the `Payments` sheet.
2. **Sequential Write Queue & Pre-Write Backups**:
   - All Excel writes pass through a singleton Promise queue (`runCollectionsExclusive`).
   - A timestamped backup copy (`data/backups/collections_YYYY-MM-DDTHH-mm-ss.xlsx`) is created before **every write**.
   - `POST /api/collections/undo-last` can revert the workbook to the previous state at any time.
3. **Deduplication**:
   - Duplicate outcomes for the same execution ID or customer within 10 seconds are automatically ignored.
4. **Compliance & Hours Safety**:
   - Calls are permitted only between **9:00 AM and 7:00 PM IST**.
   - Maximum **1 call per customer per day**.
   - Customers with active disputes (`DisputeFlag = Yes`), invalid numbers (`PhoneInvalid = Yes`), or future promise dates are skipped.
   - When `TEST_MODE=true`, compliance restrictions can be bypassed for testing, routing all calls safely to `DEFAULT_CUSTOMER_PHONE`.

---

## 3. Exact Voice AI Custom Function JSON

To configure or inspect the custom function in your Voice Collections Agent (Agent ID: `your_collections_agent_id`), use the following exact JSON configuration under `tools_config.api_tools`:

### Tool Definition (`tools` array):
```json
{
  "key": "custom_task",
  "name": "log_collection_outcome",
  "description": "Use this function ONCE when the customer's answer is clear, to record the result of the collection call. Call it before ending the call. Outcomes: promise_to_pay (customer commits to pay on a date), payment_claimed (customer says they already paid or are paying right now), callback_requested (asks to be called later), extension_requested (cannot pay, asks for more time), dispute (disagrees with the bill), wrong_person (wrong number or not the customer), refused (clearly refuses to pay).",
  "pre_call_message": "One moment, noting that down.",
  "parameters": {
    "type": "object",
    "required": [
      "customer_id",
      "outcome",
      "amount",
      "date",
      "payment_mode",
      "callback_time",
      "reason"
    ],
    "properties": {
      "customer_id": {
        "type": "string",
        "description": "The customer ID given in your instructions. Never change it."
      },
      "outcome": {
        "type": "string",
        "description": "Exactly one of: promise_to_pay, payment_claimed, callback_requested, extension_requested, dispute, wrong_person, refused."
      },
      "amount": {
        "type": "string",
        "description": "Rupee amount the customer promised or says they paid, as plain digits with no symbols. Convert 20k to 20000. Send none if not mentioned."
      },
      "date": {
        "type": "string",
        "description": "The date the customer gave (promise date, or the new date asked for), in YYYY-MM-DD format, worked out from today's date in your instructions. Send none if not mentioned."
      },
      "payment_mode": {
        "type": "string",
        "description": "UPI, cash, bank transfer or cheque if the customer mentioned it. Otherwise send none."
      },
      "callback_time": {
        "type": "string",
        "description": "The time the customer wants a call back, for example 5 PM tomorrow. Otherwise send none."
      },
      "reason": {
        "type": "string",
        "description": "A short reason in plain words if the customer disputes, refuses, or asks for more time. Otherwise send none."
      }
    }
  }
}
```

### Parameter Mapping (`tools_params` object):
```json
{
  "log_collection_outcome": {
    "url": "https://<your-active-tunnel-domain>/api/collections/outcome",
    "method": "POST",
    "headers": {
      "Content-Type": "application/json"
    },
    "api_token": "Bearer <YOUR_LEDGER_SECRET>",
    "param": {
      "customer_id": "%(customer_id)s",
      "outcome": "%(outcome)s",
      "amount": "%(amount)s",
      "date": "%(date)s",
      "payment_mode": "%(payment_mode)s",
      "callback_time": "%(callback_time)s",
      "reason": "%(reason)s"
    }
  }
}
```

---

## 4. Curl Tests

### 1. Test Outcome (Promise to Pay)
```bash
curl -X POST http://localhost:8080/api/collections/outcome \
  -H "Authorization: Bearer <YOUR_LEDGER_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST0445",
    "outcome": "promise_to_pay",
    "amount": "50000",
    "date": "2026-11-25",
    "payment_mode": "UPI",
    "callback_time": "none",
    "reason": "none"
  }'
```

### 2. Test Outcome (Payment Claimed)
```bash
curl -X POST http://localhost:8080/api/collections/outcome \
  -H "Authorization: Bearer <YOUR_LEDGER_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST0851",
    "outcome": "payment_claimed",
    "amount": "138196.75",
    "date": "none",
    "payment_mode": "NEFT",
    "callback_time": "none",
    "reason": "none"
  }'
```

### 3. Verify Payment
```bash
curl -X POST http://localhost:8080/api/collections/verify \
  -H "Content-Type: application/json" \
  -d '{
    "customerId": "CUST0851",
    "amount": 138196.75
  }'
```

### 4. Reject Claim
```bash
curl -X POST http://localhost:8080/api/collections/reject-claim \
  -H "Content-Type: application/json" \
  -d '{
    "customerId": "CUST0851"
  }'
```

### 5. Outbound Call Trigger
```bash
curl -X POST http://localhost:8080/api/collections/call \
  -H "Content-Type: application/json" \
  -d '{
    "customerId": "CUST0445"
  }'
```

### 6. Email Reminder (Dry run or SMTP)
```bash
curl -X POST http://localhost:8080/api/collections/email-reminder \
  -H "Content-Type: application/json" \
  -d '{
    "customerId": "CUST0445"
  }'
```

### 7. Undo Last Operation
```bash
curl -X POST http://localhost:8080/api/collections/undo-last
```

<!-- Updated: 2026-11-22 -->
