const express = require('express');
const ExcelJS = require('exceljs');
const fs = require('fs');
const path = require('path');
const nodemailer = require('nodemailer');
require('dotenv').config();

process.on('uncaughtException', err => {
  console.error('[UNCAUGHT EXCEPTION]:', err);
});
process.on('unhandledRejection', (reason, promise) => {
  console.error('[UNHANDLED REJECTION]:', reason);
});

const app = express();
const PORT = process.env.NODE_PORT || 3000;
const DATA_DIR = path.join(__dirname, 'data');
const LEDGER_PATH = path.join(DATA_DIR, 'ledger.xlsx');
const COLLECTIONS_PATH = path.join(DATA_DIR, 'collections.xlsx');
const BACKUPS_DIR = path.join(DATA_DIR, 'backups');

app.use(express.json());
app.use(express.static(__dirname));

// Ensure directories exist
if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}
if (!fs.existsSync(BACKUPS_DIR)) {
  fs.mkdirSync(BACKUPS_DIR, { recursive: true });
}

// ─────────────────────────────────────────────────────────────────────────────
// 1. LEDGER AGENT HELPERS (EXISTING - PRESERVED)
// ─────────────────────────────────────────────────────────────────────────────
async function initLedgerExcel() {
  const workbook = new ExcelJS.Workbook();
  if (fs.existsSync(LEDGER_PATH)) {
    await workbook.xlsx.readFile(LEDGER_PATH);
  } else {
    const custSheet = workbook.addWorksheet('Customers');
    custSheet.addRow(['Name', 'Phone', 'Balance']);

    const ledgerSheet = workbook.addWorksheet('Ledger');
    ledgerSheet.addRow(['Date', 'Customer', 'Type', 'Amount', 'Balance', 'Notes']);

    await workbook.xlsx.writeFile(LEDGER_PATH);
  }
  return workbook;
}

async function recordTransactionToExcel({ customer_name, type, amount, phone = '', notes = '' }) {
  const workbook = new ExcelJS.Workbook();
  if (fs.existsSync(LEDGER_PATH)) {
    await workbook.xlsx.readFile(LEDGER_PATH);
  } else {
    await initLedgerExcel();
    await workbook.xlsx.readFile(LEDGER_PATH);
  }

  let custSheet = workbook.getWorksheet('Customers');
  if (!custSheet) {
    custSheet = workbook.addWorksheet('Customers');
    custSheet.addRow(['Name', 'Phone', 'Balance']);
  }

  let ledgerSheet = workbook.getWorksheet('Ledger');
  if (!ledgerSheet) {
    ledgerSheet = workbook.addWorksheet('Ledger');
    ledgerSheet.addRow(['Date', 'Customer', 'Type', 'Amount', 'Balance', 'Notes']);
  }

  let customerRow = null;
  let currentBalance = 0;

  custSheet.eachRow((row, rowNumber) => {
    if (rowNumber === 1) return;
    const nameVal = row.getCell(1).value;
    if (nameVal && String(nameVal).trim().toLowerCase() === customer_name.trim().toLowerCase()) {
      customerRow = row;
      currentBalance = parseFloat(row.getCell(3).value) || 0;
    }
  });

  let newBalance = currentBalance;
  if (type === 'credit_given') {
    newBalance = currentBalance + amount;
  } else if (type === 'payment_received') {
    newBalance = currentBalance - amount;
  }

  if (customerRow) {
    customerRow.getCell(3).value = newBalance;
    if (phone && !customerRow.getCell(2).value) {
      customerRow.getCell(2).value = phone;
    }
  } else {
    custSheet.addRow([customer_name.trim(), phone, newBalance]);
  }

  const nowStr = new Date().toISOString().replace('T', ' ').substring(0, 19);
  ledgerSheet.addRow([nowStr, customer_name.trim(), type, amount, newBalance, notes || '']);

  await workbook.xlsx.writeFile(LEDGER_PATH);

  try {
    await workbook.xlsx.writeFile(path.join(__dirname, 'ledger.xlsx'));
  } catch (err) {}

  return { newBalance, date: nowStr };
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. COLLECTIONS AGENT HELPERS & EXCEL SAFETY
// ─────────────────────────────────────────────────────────────────────────────

// Formatting helpers
function formatINR(val) {
  const num = parseFloat(val) || 0;
  return '₹' + num.toLocaleString('en-IN');
}

function formatDateTime(d = new Date()) {
  const pad = n => String(n).padStart(2, '0');
  const Y = d.getFullYear();
  const M = pad(d.getMonth() + 1);
  const D = pad(d.getDate());
  const h = pad(d.getHours());
  const m = pad(d.getMinutes());
  const s = pad(d.getSeconds());
  return `${Y}-${M}-${D} ${h}:${m}:${s}`;
}

function formatDateOnly(d = new Date()) {
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function addDaysToDate(dateStr, days) {
  let d = dateStr ? new Date(dateStr) : new Date();
  if (isNaN(d.getTime())) d = new Date();
  d.setDate(d.getDate() + days);
  return formatDateOnly(d);
}

// Check allowed calling hours: 9 AM to 7 PM IST
function isWithinOperatingHours() {
  try {
    const istHour = parseInt(new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Kolkata',
      hour: 'numeric',
      hour12: false
    }).format(new Date()), 10);
    return istHour >= 9 && istHour < 19;
  } catch (e) {
    const h = (new Date().getUTCHours() + 5.5) % 24;
    return h >= 9 && h < 19;
  }
}

// Auto-backup before every write
function backupCollectionsExcel() {
  if (!fs.existsSync(COLLECTIONS_PATH)) return;
  const ts = new Date().toISOString().replace(/[:.]/g, '-');
  const backupFile = path.join(BACKUPS_DIR, `collections_${ts}.xlsx`);
  fs.copyFileSync(COLLECTIONS_PATH, backupFile);

  // Maintain max 50 backups
  try {
    const files = fs.readdirSync(BACKUPS_DIR)
      .filter(f => f.startsWith('collections_') && f.endsWith('.xlsx'))
      .map(f => ({ file: f, time: fs.statSync(path.join(BACKUPS_DIR, f)).mtimeMs }))
      .sort((a, b) => b.time - a.time);
    if (files.length > 50) {
      for (let i = 50; i < files.length; i++) {
        fs.unlinkSync(path.join(BACKUPS_DIR, files[i].file));
      }
    }
  } catch (e) {}
}

// Promise queue to ensure single-threaded sequential Excel writes
let collectionsQueue = Promise.resolve();
function runCollectionsExclusive(taskFn) {
  const p = collectionsQueue.then(async () => {
    backupCollectionsExcel();
    return await taskFn();
  });
  collectionsQueue = p.catch(err => {
    console.error('Collections Excel write queue error:', err);
  });
  return p;
}

// Format sheets: bold header, frozen row, column widths, colored status
function applySheetFormatting(sheet, isReceivables = false) {
  sheet.views = [{ state: 'frozen', xSplit: 0, ySplit: 1 }];

  // Header row styling
  const headerRow = sheet.getRow(1);
  headerRow.font = { bold: true, color: { argb: 'FF1E293B' }, size: 10 };
  headerRow.fill = {
    type: 'pattern',
    pattern: 'solid',
    fgColor: { argb: 'FFF1F5F9' }
  };
  headerRow.alignment = { vertical: 'middle', horizontal: 'left' };
  headerRow.height = 24;

  // Auto column widths
  if (sheet.columns) {
    sheet.columns.forEach(column => {
      let maxLength = 14;
      if (typeof column.eachCell === 'function') {
        column.eachCell({ includeEmpty: false }, cell => {
          const valStr = cell.value ? String(cell.value) : '';
          if (valStr.length > maxLength) {
            maxLength = Math.min(valStr.length + 3, 40);
          }
        });
      }
      column.width = maxLength;
    });
  }

  // If Receivables sheet, format status cells
  if (isReceivables) {
    const statusColIdx = 11; // Column K is Status
    const statusColors = {
      'Follow-up pending': { fg: 'FFF59E0B', bg: 'FFFEF3C7' },
      'Promise made': { fg: 'FF0284C7', bg: 'FFE0F2FE' },
      'Payment claimed (verify)': { fg: 'FF7C3AED', bg: 'FFF3E8FF' },
      'Callback scheduled': { fg: 'FF2563EB', bg: 'FFDBEAFE' },
      'Disputed': { fg: 'FFDC2626', bg: 'FFFEE2E2' },
      'Do not call': { fg: 'FF64748B', bg: 'FFF1F5F9' },
      'Resolved': { fg: 'FF16A34A', bg: 'FFDCFCE7' },
      'Needs approval': { fg: 'FFD97706', bg: 'FFFFFBEB' },
      'Unresolved': { fg: 'FFB91C1C', bg: 'FFFEE2E2' }
    };

    sheet.eachRow((row, rowNumber) => {
      if (rowNumber === 1) return;
      const statusCell = row.getCell(statusColIdx);
      const statusVal = statusCell.value ? String(statusCell.value).trim() : '';
      if (statusColors[statusVal]) {
        statusCell.font = { bold: true, color: { argb: statusColors[statusVal].fg } };
        statusCell.fill = {
          type: 'pattern',
          pattern: 'solid',
          fgColor: { argb: statusColors[statusVal].bg }
        };
      }
    });
  }
}

async function getCollectionsWorkbook() {
  const workbook = new ExcelJS.Workbook();
  if (fs.existsSync(COLLECTIONS_PATH)) {
    await workbook.xlsx.readFile(COLLECTIONS_PATH);
  } else {
    // 1. Receivables Sheet
    const recSheet = workbook.addWorksheet('Receivables');
    recSheet.addRow([
      'CustomerID', 'Name', 'Phone', 'Email', 'InvoiceID', 'InvoiceDate', 'AmountPending',
      'DueDate', 'DaysOverdue', 'RiskTier', 'Status', 'PromiseAmount', 'PromiseDate',
      'PaymentClaimed', 'ClaimedAmount', 'PaymentMode', 'CallbackTime', 'DisputeFlag',
      'DisputeReason', 'PhoneInvalid', 'AttemptCount', 'LastContact', 'NextFollowUp', 'Dismissed'
    ]);
    applySheetFormatting(recSheet, true);

    // 2. CollectionsLog Sheet
    const logSheet = workbook.addWorksheet('CollectionsLog');
    logSheet.addRow([
      'Timestamp', 'CustomerID', 'Name', 'InvoiceID', 'Outcome', 'AmountPromised',
      'PromiseDate', 'ClaimedAmount', 'PaymentMode', 'PendingBefore', 'PendingAfter',
      'CallbackTime', 'Reason', 'Notes', 'CallStatus', 'DurationSec', 'ExecutionId',
      'VerifiedBy', 'Source'
    ]);
    applySheetFormatting(logSheet, false);

    // 3. Payments Sheet
    const paySheet = workbook.addWorksheet('Payments');
    paySheet.addRow(['Date', 'CustomerID', 'Name', 'Amount', 'Mode', 'VerifiedBy']);
    applySheetFormatting(paySheet, false);

    await workbook.xlsx.writeFile(COLLECTIONS_PATH);
  }
  return workbook;
}

async function saveCollectionsWorkbook(workbook) {
  const recSheet = workbook.getWorksheet('Receivables');
  if (recSheet) applySheetFormatting(recSheet, true);
  const logSheet = workbook.getWorksheet('CollectionsLog');
  if (logSheet) applySheetFormatting(logSheet, false);
  const paySheet = workbook.getWorksheet('Payments');
  if (paySheet) applySheetFormatting(paySheet, false);

  await workbook.xlsx.writeFile(COLLECTIONS_PATH);
}

// Deduplication store for execution outcomes within 10 seconds
const recentOutcomes = new Map(); // key -> timestamp
const activeCallWatchers = new Map(); // executionId -> status details

// Background post-call watcher
async function pollBolnaExecution(executionId, customerId) {
  const bolnaApiKey = process.env.VOICE_API_KEY || process.env.API_KEY || process.env.BOLNA_API_KEY;
  if (!bolnaApiKey || !executionId) return;

  let pollCount = 0;
  const maxPolls = 120; // 10 minutes (120 * 5s)

  activeCallWatchers.set(executionId, { status: 'in-progress', startedAt: Date.now() });

  const timer = setInterval(async () => {
    pollCount++;
    if (pollCount > maxPolls) {
      clearInterval(timer);
      activeCallWatchers.delete(executionId);
      return;
    }

    try {
      const res = await fetch(`https://api.bolna.ai/executions/${executionId}`, {
        headers: { 'Authorization': `Bearer ${bolnaApiKey}` }
      });
      if (!res.ok) return;

      const data = await res.json();
      const status = (data.status || '').toLowerCase();

      // In-progress states
      if (status === 'in-progress' || status === 'ringing' || status === 'queued' || status === 'initiated') {
        activeCallWatchers.set(executionId, {
          status: 'in-progress',
          duration: data.conversation_duration || 0
        });
        return;
      }

      // Terminal completed states (including call-disconnected)
      if (status === 'completed' || status === 'done' || status === 'call-disconnected') {
        clearInterval(timer);
        const duration = Math.round(data.conversation_duration || (data.telephony_data && data.telephony_data.duration) || 0);
        const summary = data.summary || 'Call completed successfully';

        activeCallWatchers.set(executionId, {
          status: 'completed',
          duration: duration,
          summary: summary
        });

        await runCollectionsExclusive(async () => {
          const workbook = await getCollectionsWorkbook();
          const logSheet = workbook.getWorksheet('CollectionsLog');
          let hasOutcome = false;

          logSheet.eachRow((row, rowNumber) => {
            if (rowNumber === 1) return;
            const exId = row.getCell(17).value;
            const outcome = row.getCell(5).value;
            if (exId === executionId && outcome && outcome !== 'call_started' && outcome !== 'call_completed_no_outcome') {
              hasOutcome = true;
            }
          });

          if (!hasOutcome) {
            const nowStr = formatDateTime();
            const recSheet = workbook.getWorksheet('Receivables');
            let custRow = null;
            recSheet.eachRow((r, idx) => {
              if (idx === 1) return;
              if (String(r.getCell(1).value).toLowerCase() === String(customerId).toLowerCase()) {
                custRow = r;
              }
            });
            const custName = custRow ? custRow.getCell(2).value : '';
            const invId = custRow ? custRow.getCell(5).value : '';
            const pending = custRow ? custRow.getCell(7).value : 0;

            logSheet.addRow([
              nowStr, customerId, custName, invId, 'call_completed_no_outcome',
              '', '', '', '', pending, pending, '', '',
              `Meera call completed (${duration}s). ${summary}`,
              'completed', duration, executionId, '', 'bolna_watcher'
            ]);
            await saveCollectionsWorkbook(workbook);
          } else {
            // Update call duration & status in log
            logSheet.eachRow((row, rowNumber) => {
              if (rowNumber === 1) return;
              if (row.getCell(17).value === executionId) {
                row.getCell(15).value = 'completed';
                row.getCell(16).value = duration;
              }
            });
            await saveCollectionsWorkbook(workbook);
          }
        });
        return;
      }

      if (['busy', 'no-answer', 'failed', 'canceled', 'cancelled', 'rejected'].includes(status)) {
        clearInterval(timer);
        activeCallWatchers.set(executionId, {
          status: 'failed',
          detail: status
        });

        await runCollectionsExclusive(async () => {
          const workbook = await getCollectionsWorkbook();
          const recSheet = workbook.getWorksheet('Receivables');
          const logSheet = workbook.getWorksheet('CollectionsLog');

          let custRow = null;
          recSheet.eachRow((r, idx) => {
            if (idx === 1) return;
            if (String(r.getCell(1).value).toLowerCase() === String(customerId).toLowerCase()) {
              custRow = r;
            }
          });

          const custName = custRow ? custRow.getCell(2).value : '';
          const invId = custRow ? custRow.getCell(5).value : '';
          const pending = custRow ? custRow.getCell(7).value : 0;

          if (custRow) {
            const curAttempts = parseInt(custRow.getCell(21).value) || 0;
            custRow.getCell(21).value = curAttempts + 1;
            custRow.getCell(22).value = formatDateTime();
            custRow.getCell(23).value = addDaysToDate(null, 1); // tomorrow
          }

          const nowStr = formatDateTime();
          logSheet.addRow([
            nowStr, customerId, custName, invId, 'no_response',
            '', '', '', '', pending, pending, '', `Call status: ${status}`,
            `Outbound call attempt: ${status}. Next follow-up tomorrow.`,
            status, 0, executionId, '', 'bolna_watcher'
          ]);
          await saveCollectionsWorkbook(workbook);
        });
        return;
      }
    } catch (err) {
      console.warn('Bolna watcher polling error:', err.message);
    }
  }, 5000);
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. COLLECTIONS AGENT API ENDPOINTS
// ─────────────────────────────────────────────────────────────────────────────

// GET /api/collections/receivables - Fetch all receivables & their timeline logs
app.get('/api/collections/receivables', async (req, res) => {
  try {
    const workbook = await getCollectionsWorkbook();
    const recSheet = workbook.getWorksheet('Receivables');
    const logSheet = workbook.getWorksheet('CollectionsLog');

    // Index logs by customer ID
    const customerLogs = {};
    if (logSheet) {
      logSheet.eachRow((row, rowNumber) => {
        if (rowNumber === 1) return;
        const cId = String(row.getCell(2).value || '').trim();
        if (!cId) return;
        if (!customerLogs[cId]) customerLogs[cId] = [];
        customerLogs[cId].push({
          timestamp: row.getCell(1).value,
          outcome: row.getCell(5).value,
          amount_promised: row.getCell(6).value,
          promise_date: row.getCell(7).value,
          claimed_amount: row.getCell(8).value,
          notes: row.getCell(14).value,
          call_status: row.getCell(15).value,
          duration: row.getCell(16).value,
          execution_id: row.getCell(17).value
        });
      });
    }

    const customers = [];
    if (recSheet) {
      recSheet.eachRow((row, rowNumber) => {
        if (rowNumber === 1) return;
        const cId = String(row.getCell(1).value || '').trim();
        if (!cId) return;

        customers.push({
          CustomerID: cId,
          Name: row.getCell(2).value || '',
          Phone: (process.env.DEFAULT_CUSTOMER_PHONE || '+916363077110').trim(),
          Email: row.getCell(4).value || '',
          InvoiceID: row.getCell(5).value || '',
          InvoiceDate: row.getCell(6).value || '',
          AmountPending: parseFloat(row.getCell(7).value) || 0,
          DueDate: row.getCell(8).value || '',
          DaysOverdue: parseInt(row.getCell(9).value) || 0,
          RiskTier: row.getCell(10).value || 'Medium',
          Status: row.getCell(11).value || 'Follow-up pending',
          PromiseAmount: parseFloat(row.getCell(12).value) || 0,
          PromiseDate: row.getCell(13).value || '',
          PaymentClaimed: row.getCell(14).value || 'No',
          ClaimedAmount: parseFloat(row.getCell(15).value) || 0,
          PaymentMode: row.getCell(16).value || '',
          CallbackTime: row.getCell(17).value || '',
          DisputeFlag: row.getCell(18).value || 'No',
          DisputeReason: row.getCell(19).value || '',
          PhoneInvalid: row.getCell(20).value || 'No',
          AttemptCount: parseInt(row.getCell(21).value) || 0,
          LastContact: row.getCell(22).value || '',
          NextFollowUp: row.getCell(23).value || '',
          Dismissed: row.getCell(24).value || 'No',
          timeline: customerLogs[cId] || []
        });
      });
    }

    res.json(customers);
  } catch (err) {
    res.status(500).json({ status: 'error', message: err.message });
  }
});

// GET /api/collections/status/:executionId - Call status polling endpoint
app.get('/api/collections/status/:executionId', async (req, res) => {
  const { executionId } = req.params;
  const statusInfo = activeCallWatchers.get(executionId);
  if (statusInfo && statusInfo.status !== 'unknown') {
    return res.json(statusInfo);
  }
  // Fallback to Bolna upstream directly
  const bolnaApiKey = process.env.VOICE_API_KEY || process.env.API_KEY || process.env.BOLNA_API_KEY;
  if (bolnaApiKey && executionId) {
    try {
      const bRes = await fetch(`https://api.bolna.ai/executions/${executionId}`, {
        headers: { 'Authorization': `Bearer ${bolnaApiKey}` }
      });
      if (bRes.ok) {
        const bData = await bRes.json();
        const st = String(bData.status || '').toLowerCase();
        const dur = bData.conversation_duration || (bData.telephony_data && bData.telephony_data.duration) || 0;
        if (['completed', 'call-disconnected', 'done'].includes(st)) {
          const result = { status: 'completed', raw_status: st, duration: dur, summary: bData.summary || '' };
          activeCallWatchers.set(executionId, result);
          return res.json(result);
        } else if (['failed', 'busy', 'no-answer'].includes(st)) {
          const result = { status: 'failed', raw_status: st, duration: dur };
          activeCallWatchers.set(executionId, result);
          return res.json(result);
        } else if (['in-progress', 'ringing', 'queued', 'initiated'].includes(st)) {
          return res.json({ status: 'in-progress', raw_status: st, duration: dur });
        }
      }
    } catch (e) {
      console.warn('Fallback Bolna status fetch error:', e.message);
    }
  }
  res.json({ status: 'unknown' });
});

// POST /api/collections/call { customerId }
app.post('/api/collections/call', async (req, res) => {
  try {
    const { customerId } = req.body;
    if (!customerId) {
      return res.status(400).json({ status: 'error', message: 'customerId is required' });
    }

    const bolnaApiKey = process.env.VOICE_API_KEY || process.env.API_KEY || process.env.BOLNA_API_KEY;
    const agentId = process.env.COLLECTIONS_AGENT_ID || process.env.AGENT_ID_PAYMENT || process.env.BOLNA_COLLECTIONS_AGENT_ID || process.env.BOLNA_AGENT_ID_PAYMENT || '';
    const isTestMode = process.env.TEST_MODE === 'true';

    if (!bolnaApiKey) {
      return res.status(500).json({ status: 'error', message: 'API key is not configured in .env' });
    }

    const workbook = await getCollectionsWorkbook();
    const recSheet = workbook.getWorksheet('Receivables');
    const logSheet = workbook.getWorksheet('CollectionsLog');

    let customer = null;
    let customerRow = null;

    recSheet.eachRow((row, rowNumber) => {
      if (rowNumber === 1) return;
      if (String(row.getCell(1).value).trim().toLowerCase() === String(customerId).trim().toLowerCase()) {
        customerRow = row;
        customer = {
          CustomerID: row.getCell(1).value,
          Name: row.getCell(2).value,
          Phone: row.getCell(3).value,
          Email: row.getCell(4).value,
          InvoiceID: row.getCell(5).value,
          InvoiceDate: row.getCell(6).value,
          AmountPending: parseFloat(row.getCell(7).value) || 0,
          DueDate: row.getCell(8).value,
          DaysOverdue: parseInt(row.getCell(9).value) || 0,
          Status: row.getCell(11).value,
          PromiseDate: row.getCell(13).value,
          DisputeFlag: row.getCell(18).value,
          PhoneInvalid: row.getCell(20).value,
          AttemptCount: parseInt(row.getCell(21).value) || 0
        };
      }
    });

    if (!customer) {
      return res.status(404).json({ status: 'error', message: `Customer with ID ${customerId} not found` });
    }

    // Safety checks
    if (!isTestMode) {
      if (!isWithinOperatingHours()) {
        return res.status(403).json({
          status: 'error',
          message: 'Calls are only permitted between 9:00 AM and 7:00 PM IST for compliance.'
        });
      }

      if (customer.DisputeFlag === 'Yes') {
        return res.status(400).json({
          status: 'error',
          message: 'Call skipped: Customer has an active dispute flagged.'
        });
      }

      if (customer.PhoneInvalid === 'Yes') {
        return res.status(400).json({
          status: 'error',
          message: 'Call skipped: Customer phone number is marked invalid.'
        });
      }

      if (customer.PromiseDate) {
        const todayStr = formatDateOnly();
        if (String(customer.PromiseDate) > todayStr) {
          return res.status(400).json({
            status: 'error',
            message: `Call skipped: Customer already has a promise to pay on ${customer.PromiseDate}.`
          });
        }
      }

      // Check max 1 call per customer per day
      const todayStr = formatDateOnly();
      let alreadyCalledToday = false;
      if (logSheet) {
        logSheet.eachRow((r, idx) => {
          if (idx === 1) return;
          const ts = String(r.getCell(1).value || '');
          const cId = String(r.getCell(2).value || '');
          const source = String(r.getCell(19).value || '');
          if (cId === String(customerId) && ts.startsWith(todayStr) && source.includes('bolna')) {
            alreadyCalledToday = true;
          }
        });
      }
      if (alreadyCalledToday) {
        return res.status(429).json({
          status: 'error',
          message: 'Call skipped: Maximum 1 call per customer per day limit reached.'
        });
      }
    }

    // Determine target phone: by default DEFAULT_CUSTOMER_PHONE should always be used
    const defaultCustPhone = (process.env.DEFAULT_CUSTOMER_PHONE || '+916363077110').trim();
    let targetPhone = req.body.phone || defaultCustPhone;
    if (!targetPhone || targetPhone.startsWith('+912648') || targetPhone.length < 10) {
      targetPhone = defaultCustPhone;
    }

    // Ensure E.164 format (+91...)
    targetPhone = targetPhone.replace(/\s+/g, '');
    if (!targetPhone.startsWith('+')) {
      targetPhone = '+91' + targetPhone.replace(/^0+/, '');
    }

    const todayDate = formatDateOnly();
    const payload = {
      agent_id: agentId,
      recipient_phone_number: targetPhone,
      user_data: {
        business_name: 'Katha AI Store',
        customer_name: customer.Name,
        customer_contact_name: customer.Name,
        customer_id: String(customer.CustomerID),
        amount_pending: String(customer.AmountPending),
        invoice_id: String(customer.InvoiceID || ''),
        due_date: String(customer.DueDate || ''),
        days_overdue: String(customer.DaysOverdue || 0),
        today_date: todayDate
      }
    };

    console.log(`[Bolna Outbound Call] Dialing ${targetPhone} for customer ${customer.Name} (${customer.CustomerID})...`);
    const response = await fetch('https://api.bolna.ai/call', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${bolnaApiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    });

    const data = await response.json();
    if (!response.ok) {
      return res.status(response.status).json({
        status: 'error',
        message: `Bolna outbound call failed: ${data.message || data.error || JSON.stringify(data)}`
      });
    }

    const executionId = data.execution_id || data.id || `call_${Date.now()}`;

    // Log call_started inside Excel
    await runCollectionsExclusive(async () => {
      const wb = await getCollectionsWorkbook();
      const logs = wb.getWorksheet('CollectionsLog');
      const nowStr = formatDateTime();
      logs.addRow([
        nowStr, customer.CustomerID, customer.Name, customer.InvoiceID,
        'call_started', '', '', '', '',
        customer.AmountPending, customer.AmountPending, '', '',
        `Outbound call initiated with Agent Meera to ${targetPhone}`,
        'in-progress', 0, executionId, '', 'bolna'
      ]);
      await saveCollectionsWorkbook(wb);
    });

    // Start post-call watcher in background
    pollBolnaExecution(executionId, customer.CustomerID);

    res.json({
      status: 'success',
      message: `Meera is calling ${customer.Name} on ${targetPhone}`,
      execution_id: executionId,
      phone: targetPhone
    });
  } catch (err) {
    res.status(500).json({ status: 'error', message: err.message });
  }
});

// POST /api/collections/outcome (called by Bolna custom function during call)
app.post('/api/collections/outcome', async (req, res) => {
  try {
    const ledgerSecret = (process.env.LEDGER_SECRET || '').trim();
    const authHeader = req.headers['authorization'] || '';
    const match = authHeader.match(/^Bearer\s+(.*)$/i);
    const token = match ? match[1].trim() : '';

    if (!ledgerSecret || token !== ledgerSecret) {
      return res.status(401).json({ status: 'error', message: 'Unauthorized: Invalid Bearer token' });
    }

    let {
      customer_id,
      outcome,
      amount,
      date,
      payment_mode,
      callback_time,
      reason,
      execution_id
    } = req.body;

    console.log('[Bolna Outcome Webhook Received]:', req.body);

    // Normalize "none" strings
    const cleanStr = v => (v === 'none' || v === 'null' || !v ? '' : String(v).trim());
    outcome = cleanStr(outcome).toLowerCase();
    amount = cleanStr(amount);
    date = cleanStr(date);
    payment_mode = cleanStr(payment_mode);
    callback_time = cleanStr(callback_time);
    reason = cleanStr(reason);
    customer_id = cleanStr(customer_id);

    // Parse amount from text (e.g. "20000", "20k", "₹50,000")
    let cleanAmount = 0;
    if (amount) {
      if (amount.toLowerCase().endsWith('k')) {
        cleanAmount = parseFloat(amount.slice(0, -1)) * 1000;
      } else {
        cleanAmount = parseFloat(amount.replace(/[^0-9.-]+/g, '')) || 0;
      }
    }

    // Ignore duplicate outcomes for the same ExecutionId or customer within 10 seconds
    const dedupKey = execution_id ? `ex_${execution_id}` : `cust_${customer_id}_${outcome}`;
    const nowMs = Date.now();
    if (recentOutcomes.has(dedupKey) && (nowMs - recentOutcomes.get(dedupKey) < 10000)) {
      console.log(`[Deduplication] Ignored duplicate outcome within 10s for key: ${dedupKey}`);
      return res.json({ status: 'success', message: 'Outcome already recorded (deduplicated).' });
    }
    recentOutcomes.set(dedupKey, nowMs);

    let readableNote = '';

    await runCollectionsExclusive(async () => {
      const workbook = await getCollectionsWorkbook();
      const recSheet = workbook.getWorksheet('Receivables');
      const logSheet = workbook.getWorksheet('CollectionsLog');

      const matchingRows = [];
      recSheet.eachRow((row, rowNumber) => {
        if (rowNumber === 1) return;
        if (String(row.getCell(1).value).trim().toLowerCase() === customer_id.toLowerCase() ||
            String(row.getCell(2).value).trim().toLowerCase() === customer_id.toLowerCase()) {
          matchingRows.push(row);
        }
      });

      if (matchingRows.length === 0) {
        throw new Error(`Customer '${customer_id}' not found in collections records`);
      }

      const custRow = matchingRows[0];
      const custName = custRow.getCell(2).value || 'Customer';
      const invId = custRow.getCell(5).value || '';
      const pendingBefore = parseFloat(custRow.getCell(7).value) || 0;
      const currentAttempts = (parseInt(custRow.getCell(21).value) || 0) + 1;
      const nowStr = formatDateTime();

      let amountPromised = '';
      let promiseDate = '';
      let claimedAmount = '';
      let mode = payment_mode || '';

      switch (outcome) {
        case 'promise_to_pay':
          amountPromised = cleanAmount > 0 ? cleanAmount : pendingBefore;
          promiseDate = date || formatDateOnly();
          readableNote = `${custName} promised ${formatINR(amountPromised)} by ${promiseDate}`;
          break;

        case 'payment_claimed':
          claimedAmount = cleanAmount > 0 ? cleanAmount : pendingBefore;
          readableNote = `${custName} claimed payment of ${formatINR(claimedAmount)} via ${mode || 'UPI'}. Verification pending.`;
          break;

        case 'callback_requested':
          readableNote = `${custName} requested callback at ${callback_time || 'Tomorrow'}.`;
          break;

        case 'extension_requested':
          readableNote = `${custName} requested extension until ${date || 'later'}. Reason: ${reason || 'Cash flow'}.`;
          break;

        case 'dispute':
          readableNote = `${custName} disputed invoice ${invId}. Reason: ${reason || 'Incorrect billing'}.`;
          break;

        case 'wrong_person':
          readableNote = `Wrong contact number for ${custName}. Marked as Do not call.`;
          break;

        case 'refused':
          readableNote = `${custName} refused to pay. Escalation recommended.`;
          break;

        default:
          readableNote = `${custName} collection outcome: ${outcome}.`;
          break;
      }

      // Apply outcome to all invoices of this customer
      for (const row of matchingRows) {
        row.getCell(21).value = currentAttempts; // AttemptCount
        row.getCell(22).value = nowStr;          // LastContact

        switch (outcome) {
          case 'promise_to_pay':
            row.getCell(11).value = 'Promise made';
            row.getCell(12).value = amountPromised;
            row.getCell(13).value = promiseDate;
            row.getCell(23).value = addDaysToDate(promiseDate, 1);
            break;
          case 'payment_claimed':
            row.getCell(11).value = 'Payment claimed (verify)';
            row.getCell(14).value = 'Yes';
            row.getCell(15).value = claimedAmount;
            row.getCell(16).value = mode || 'UPI';
            break;
          case 'callback_requested':
            row.getCell(11).value = 'Callback scheduled';
            row.getCell(17).value = callback_time || 'Tomorrow';
            break;
          case 'extension_requested':
            row.getCell(11).value = 'Needs approval';
            break;
          case 'dispute':
            row.getCell(11).value = 'Disputed';
            row.getCell(18).value = 'Yes';
            row.getCell(19).value = reason || 'Customer disputes invoice';
            break;
          case 'wrong_person':
            row.getCell(11).value = 'Do not call';
            row.getCell(20).value = 'Yes';
            break;
          case 'refused':
            row.getCell(11).value = 'Unresolved';
            break;
          default:
            row.getCell(11).value = 'Follow-up pending';
            break;
        }
      }

      // Append entry to CollectionsLog sheet
      logSheet.addRow([
        nowStr,
        custRow.getCell(1).value,
        custName,
        invId,
        outcome,
        amountPromised,
        promiseDate,
        claimedAmount,
        mode,
        pendingBefore,
        pendingBefore, // PendingAfter unchanged during call
        callback_time,
        reason,
        readableNote,
        'completed',
        0,
        execution_id || '',
        '',
        'bolna_call'
      ]);

      await saveCollectionsWorkbook(workbook);
    });

    res.json({
      status: 'success',
      message: readableNote,
      outcome: outcome,
      customer_id: customer_id
    });
  } catch (err) {
    console.error('Error handling collections outcome:', err);
    res.status(500).json({ status: 'error', message: err.message });
  }
});

// POST /api/collections/verify { customerId, amount }
app.post('/api/collections/verify', async (req, res) => {
  try {
    const { customerId, amount } = req.body;
    if (!customerId) {
      return res.status(400).json({ status: 'error', message: 'customerId is required' });
    }

    let pendingAfter = 0;
    let verifiedAmt = 0;
    let customerName = '';

    await runCollectionsExclusive(async () => {
      const workbook = await getCollectionsWorkbook();
      const recSheet = workbook.getWorksheet('Receivables');
      const logSheet = workbook.getWorksheet('CollectionsLog');
      const paySheet = workbook.getWorksheet('Payments');

      let custRow = null;
      recSheet.eachRow((row, rowNumber) => {
        if (rowNumber === 1) return;
        if (String(row.getCell(1).value).trim().toLowerCase() === String(customerId).trim().toLowerCase()) {
          custRow = row;
        }
      });

      if (!custRow) {
        throw new Error(`Customer ${customerId} not found`);
      }

      customerName = custRow.getCell(2).value;
      const invId = custRow.getCell(5).value;
      const pendingBefore = parseFloat(custRow.getCell(7).value) || 0;
      const claimedAmt = parseFloat(custRow.getCell(15).value) || 0;
      const mode = custRow.getCell(16).value || 'Verified';

      verifiedAmt = amount ? parseFloat(amount) : (claimedAmt > 0 ? claimedAmt : pendingBefore);
      pendingAfter = Math.max(0, pendingBefore - verifiedAmt); // Never below 0

      // Update customer in Receivables
      custRow.getCell(7).value = pendingAfter;
      custRow.getCell(14).value = 'No';  // PaymentClaimed = No
      custRow.getCell(15).value = '';    // ClaimedAmount = ''
      custRow.getCell(11).value = pendingAfter === 0 ? 'Resolved' : 'Follow-up pending';
      custRow.getCell(22).value = formatDateTime();

      const nowStr = formatDateTime();
      const todayStr = formatDateOnly();

      // Add row to Payments sheet
      paySheet.addRow([
        todayStr,
        customerId,
        customerName,
        verifiedAmt,
        mode,
        'owner'
      ]);

      // Append to CollectionsLog
      logSheet.addRow([
        nowStr,
        customerId,
        customerName,
        invId,
        'payment_received',
        '',
        '',
        verifiedAmt,
        mode,
        pendingBefore,
        pendingAfter,
        '',
        'Payment verified by owner',
        `Owner verified payment of ${formatINR(verifiedAmt)}. Remaining pending: ${formatINR(pendingAfter)}`,
        'completed',
        0,
        '',
        'owner',
        'manual'
      ]);

      await saveCollectionsWorkbook(workbook);
    });

    res.json({
      status: 'success',
      message: `Verified payment of ${formatINR(verifiedAmt)} for ${customerName}. Remaining balance: ${formatINR(pendingAfter)}.`,
      pending_before: pendingAfter + verifiedAmt,
      pending_after: pendingAfter
    });
  } catch (err) {
    res.status(500).json({ status: 'error', message: err.message });
  }
});

// POST /api/collections/reject-claim { customerId }
app.post('/api/collections/reject-claim', async (req, res) => {
  try {
    const { customerId } = req.body;
    if (!customerId) {
      return res.status(400).json({ status: 'error', message: 'customerId is required' });
    }

    await runCollectionsExclusive(async () => {
      const workbook = await getCollectionsWorkbook();
      const recSheet = workbook.getWorksheet('Receivables');
      const logSheet = workbook.getWorksheet('CollectionsLog');

      let custRow = null;
      recSheet.eachRow((row, rowNumber) => {
        if (rowNumber === 1) return;
        if (String(row.getCell(1).value).trim().toLowerCase() === String(customerId).trim().toLowerCase()) {
          custRow = row;
        }
      });

      if (!custRow) throw new Error(`Customer ${customerId} not found`);

      const custName = custRow.getCell(2).value;
      const invId = custRow.getCell(5).value;
      const pending = parseFloat(custRow.getCell(7).value) || 0;

      custRow.getCell(11).value = 'Follow-up pending';
      custRow.getCell(14).value = 'No';
      custRow.getCell(15).value = '';
      custRow.getCell(22).value = formatDateTime();

      const nowStr = formatDateTime();
      logSheet.addRow([
        nowStr,
        customerId,
        custName,
        invId,
        'claim_rejected',
        '',
        '',
        '',
        '',
        pending,
        pending,
        '',
        'Claim rejected by owner',
        `Owner rejected payment claim for ${custName}. Reverted to Follow-up pending.`,
        'completed',
        0,
        '',
        'owner',
        'manual'
      ]);

      await saveCollectionsWorkbook(workbook);
    });

    res.json({ status: 'success', message: 'Payment claim rejected and status reverted to Follow-up pending.' });
  } catch (err) {
    res.status(500).json({ status: 'error', message: err.message });
  }
});

// POST /api/collections/undo-last
app.post('/api/collections/undo-last', async (req, res) => {
  try {
    if (!fs.existsSync(BACKUPS_DIR)) {
      return res.status(400).json({ status: 'error', message: 'No backups directory found.' });
    }

    const backups = fs.readdirSync(BACKUPS_DIR)
      .filter(f => f.startsWith('collections_') && f.endsWith('.xlsx'))
      .map(f => ({ file: f, time: fs.statSync(path.join(BACKUPS_DIR, f)).mtimeMs }))
      .sort((a, b) => b.time - a.time);

    if (backups.length === 0) {
      return res.status(400).json({ status: 'error', message: 'No Excel backup available to undo.' });
    }

    const latestBackup = path.join(BACKUPS_DIR, backups[0].file);
    fs.copyFileSync(latestBackup, COLLECTIONS_PATH);

    res.json({
      status: 'success',
      message: `Successfully restored collections data from backup (${backups[0].file}).`
    });
  } catch (err) {
    res.status(500).json({ status: 'error', message: err.message });
  }
});

// POST /api/collections/email-reminder { customerId }
app.post('/api/collections/email-reminder', async (req, res) => {
  try {
    const { customerId } = req.body;
    if (!customerId) {
      return res.status(400).json({ status: 'error', message: 'customerId is required' });
    }

    const workbook = await getCollectionsWorkbook();
    const recSheet = workbook.getWorksheet('Receivables');

    let customer = null;
    recSheet.eachRow((row, rowNumber) => {
      if (rowNumber === 1) return;
      if (String(row.getCell(1).value).trim().toLowerCase() === String(customerId).trim().toLowerCase()) {
        customer = {
          CustomerID: row.getCell(1).value,
          Name: row.getCell(2).value,
          Email: row.getCell(4).value,
          InvoiceID: row.getCell(5).value,
          AmountPending: parseFloat(row.getCell(7).value) || 0,
          DueDate: row.getCell(8).value,
          DaysOverdue: parseInt(row.getCell(9).value) || 0
        };
      }
    });

    if (!customer) {
      return res.status(404).json({ status: 'error', message: `Customer ${customerId} not found` });
    }

    const smtpHost = process.env.SMTP_HOST;
    const smtpUser = process.env.SMTP_USER;
    const smtpPass = process.env.SMTP_PASS;
    const fromEmail = process.env.FROM_EMAIL || 'accounts@kathaai.com';
    const isSmtpConfigured = Boolean(smtpHost && smtpUser && smtpPass);

    const emailSubject = `Payment Reminder: Invoice #${customer.InvoiceID} (${customer.Name})`;
    const emailBody = `Dear ${customer.Name},

This is a gentle reminder regarding your outstanding invoice #${customer.InvoiceID} for the amount of ${formatINR(customer.AmountPending)}, which was due on ${customer.DueDate} (${customer.DaysOverdue} days overdue).

Kindly arrange the payment at your earliest convenience or let us know if you have any questions or require an updated invoice copy.

Warm regards,
Katha AI Finance Team`;

    let dryRun = false;
    let message = '';

    if (isSmtpConfigured) {
      const transporter = nodemailer.createTransport({
        host: smtpHost,
        port: parseInt(process.env.SMTP_PORT) || 587,
        secure: process.env.SMTP_SECURE === 'true',
        auth: { user: smtpUser, pass: smtpPass }
      });

      await transporter.sendMail({
        from: fromEmail,
        to: customer.Email || fromEmail,
        subject: emailSubject,
        text: emailBody
      });
      message = `Payment reminder email sent to ${customer.Name} (${customer.Email || 'default email'}).`;
    } else {
      dryRun = true;
      message = `SMTP not configured in .env. Dry run completed: Polite reminder email drafted for ${customer.Name} (${formatINR(customer.AmountPending)}).`;
    }

    // Log the reminder in CollectionsLog & update LastContact
    await runCollectionsExclusive(async () => {
      const wb = await getCollectionsWorkbook();
      const rec = wb.getWorksheet('Receivables');
      const logs = wb.getWorksheet('CollectionsLog');

      rec.eachRow((r, idx) => {
        if (idx === 1) return;
        if (String(r.getCell(1).value).toLowerCase() === String(customerId).toLowerCase()) {
          r.getCell(22).value = formatDateTime(); // LastContact
        }
      });

      const nowStr = formatDateTime();
      logs.addRow([
        nowStr,
        customer.CustomerID,
        customer.Name,
        customer.InvoiceID,
        'reminder_sent_email',
        '',
        '',
        '',
        '',
        customer.AmountPending,
        customer.AmountPending,
        '',
        '',
        `Reminder sent (Email) - ${dryRun ? 'Dry Run' : 'Sent via SMTP'}`,
        'completed',
        0,
        '',
        'owner',
        'email'
      ]);

      await saveCollectionsWorkbook(wb);
    });

    res.json({
      status: 'success',
      dry_run: dryRun,
      message: message,
      email_preview: { subject: emailSubject, body: emailBody }
    });
  } catch (err) {
    res.status(500).json({ status: 'error', message: err.message });
  }
});

// POST /api/collections/dismiss { customerId }
app.post('/api/collections/dismiss', async (req, res) => {
  try {
    const { customerId } = req.body;
    if (!customerId) {
      return res.status(400).json({ status: 'error', message: 'customerId is required' });
    }

    await runCollectionsExclusive(async () => {
      const workbook = await getCollectionsWorkbook();
      const recSheet = workbook.getWorksheet('Receivables');
      const logSheet = workbook.getWorksheet('CollectionsLog');

      let custRow = null;
      recSheet.eachRow((row, rowNumber) => {
        if (rowNumber === 1) return;
        if (String(row.getCell(1).value).trim().toLowerCase() === String(customerId).trim().toLowerCase()) {
          custRow = row;
        }
      });

      if (!custRow) throw new Error(`Customer ${customerId} not found`);

      const custName = custRow.getCell(2).value;
      const invId = custRow.getCell(5).value;
      const pending = parseFloat(custRow.getCell(7).value) || 0;

      custRow.getCell(24).value = 'Yes'; // Dismissed = Yes
      custRow.getCell(23).value = addDaysToDate(null, 7); // Hide recommendation for 7 days

      const nowStr = formatDateTime();
      logSheet.addRow([
        nowStr,
        customerId,
        custName,
        invId,
        'dismissed',
        '',
        '',
        '',
        '',
        pending,
        pending,
        '',
        'Dismissed by user',
        `Recommendation for ${custName} dismissed for 7 days.`,
        'completed',
        0,
        '',
        'owner',
        'manual'
      ]);

      await saveCollectionsWorkbook(workbook);
    });

    res.json({ status: 'success', message: 'Customer recommendation dismissed for 7 days.' });
  } catch (err) {
    res.status(500).json({ status: 'error', message: err.message });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// 4. LEDGER AGENT ENDPOINTS (EXISTING - PRESERVED)
// ─────────────────────────────────────────────────────────────────────────────
const VALID_TYPES = ['credit_given', 'payment_received', 'payment_missed'];

app.post('/api/ledger-call', async (req, res) => {
  try {
    const bolnaApiKey = process.env.VOICE_API_KEY || process.env.API_KEY || process.env.BOLNA_API_KEY;
    const agentId = process.env.LEDGER_AGENT_ID || process.env.AGENT_ID_LEDGER || process.env.BOLNA_LEDGER_AGENT_ID || process.env.BOLNA_AGENT_ID_LEDGER || '';
    const ownerPhone = req.body.phone || process.env.OWNER_PHONE;

    if (!bolnaApiKey) {
      return res.status(500).json({ status: 'error', message: 'API key is not configured in .env' });
    }
    if (!ownerPhone) {
      return res.status(400).json({ status: 'error', message: 'OWNER_PHONE is required in .env or request body' });
    }

    const payload = {
      agent_id: agentId,
      recipient_phone_number: ownerPhone,
      user_data: {
        user_name: 'Arjun Mehta',
        customer_name: 'Arjun Mehta',
        business_name: 'Katha AI Store',
        language: req.body.language || 'hi',
        timezone: 'Asia/Kolkata'
      }
    };

    const response = await fetch('https://api.bolna.ai/call', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${bolnaApiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    });

    const data = await response.json();
    if (!response.ok) {
      return res.status(response.status).json({
        status: 'error',
        message: `Voice Call failed: ${data.message || data.error || JSON.stringify(data)}`
      });
    }

    res.json({
      status: 'success',
      message: `Ledger Agent is calling ${ownerPhone} to update the ledger!`,
      execution_id: data.execution_id || data.id,
      call_status: data.status || 'queued'
    });
  } catch (err) {
    res.status(500).json({ status: 'error', message: err.message });
  }
});

app.post('/api/ledger', async (req, res) => {
  try {
    const ledgerSecret = (process.env.LEDGER_SECRET || '').trim();
    const authHeader = req.headers['authorization'] || '';
    const match = authHeader.match(/^Bearer\s+(.*)$/i);
    const token = match ? match[1].trim() : '';

    if (!ledgerSecret || token !== ledgerSecret) {
      return res.status(401).json({ status: 'error', message: 'Unauthorized: Invalid Bearer token' });
    }

    const { customer_name, type, amount: rawAmount, phone, notes } = req.body;

    if (!customer_name || !String(customer_name).trim()) {
      return res.status(400).json({ status: 'error', message: 'customer_name is required' });
    }

    const cleanType = String(type || '').trim().toLowerCase();
    if (!VALID_TYPES.includes(cleanType)) {
      return res.status(400).json({
        status: 'error',
        message: `Invalid type '${type}'. Must be one of: credit_given, payment_received, payment_missed.`
      });
    }

    const cleanAmount = parseFloat(String(rawAmount).replace(/[^0-9.-]+/g, ''));
    if (isNaN(cleanAmount) || cleanAmount <= 0) {
      return res.status(400).json({
        status: 'error',
        message: `Amount must be a valid number greater than 0. Received: ${rawAmount}`
      });
    }

    const { newBalance, date } = await recordTransactionToExcel({
      customer_name,
      type: cleanType,
      amount: cleanAmount,
      phone,
      notes
    });

    const displayType = cleanType.replace('_', ' ');
    res.json({
      status: 'success',
      message: `Updated ledger in Excel. ${customer_name}: recorded ${displayType} of ₹${cleanAmount.toLocaleString('en-IN')}. Current balance is ₹${newBalance.toLocaleString('en-IN')}.`,
      customer_name,
      type: cleanType,
      amount: cleanAmount,
      balance: newBalance,
      date
    });
  } catch (err) {
    res.status(500).json({ status: 'error', message: err.message });
  }
});

app.get('/api/ledger/recent', async (req, res) => {
  try {
    if (!fs.existsSync(LEDGER_PATH)) {
      return res.json([]);
    }
    const workbook = new ExcelJS.Workbook();
    await workbook.xlsx.readFile(LEDGER_PATH);
    const ledgerSheet = workbook.getWorksheet('Ledger');
    if (!ledgerSheet) return res.json([]);

    const results = [];
    ledgerSheet.eachRow((row, rowNumber) => {
      if (rowNumber === 1) return;
      results.push({
        id: rowNumber - 1,
        created_at: row.getCell(1).value,
        customer_name: row.getCell(2).value,
        type: row.getCell(3).value,
        amount: parseFloat(row.getCell(4).value) || 0,
        balance: parseFloat(row.getCell(5).value) || 0,
        notes: row.getCell(6).value || ''
      });
    });

    res.json(results.reverse().slice(0, 50));
  } catch (err) {
    res.status(500).json({ status: 'error', message: err.message });
  }
});

// Error handling middleware to catch malformed JSON and other errors
app.use((err, req, res, next) => {
  if (err) {
    console.error('Express caught error:', err.message);
    return res.status(err.status || 400).json({ status: 'error', message: err.message });
  }
  next();
});

app.listen(PORT, () => {
  console.log(`SME Ledger & Collections Server running on port ${PORT}`);
});

// Updated: 2026-11-22
