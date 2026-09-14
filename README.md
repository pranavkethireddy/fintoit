# Fintoit 💰
### The AI CFO Platform for Startups

Fintoit is a full-stack financial management platform built for early-stage startups. It combines real-time financial tracking, AI-powered insights, and CFO-grade tools into a single web app — so founders can stay on top of their finances without hiring a CFO.

---

## Features

**Dashboard & Analytics** — Real-time cash balance, burn rate, runway, and MRR with interactive charts. AI anomaly detection flags spending spikes and revenue drops automatically. 6-month cash flow forecasting based on recent trends.

**AI Tools** — Groq-powered financial insights, a floating AI chat assistant aware of your live data, smart transaction auto-categorization, scenario planning ("what if" modeling), and a one-click investor report PDF generator with AI-written narrative.

**Accounting** — Full double-entry chart of accounts, balance sheet, income statement, cash flow statement, invoices with automatic email delivery, bills tracking, AR aging report, and tax document storage with PDF upload.

**Payroll & Equity** — Employee directory with salary and equity tracking, pay run history, and a full cap table with ownership percentages, shareholder types, and a doughnut ownership chart.

**Operations** — Vendor management with spend tracking, budget planning with custom date ranges, financial goals with progress charts, and recurring transaction automation.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask, Flask-Login |
| Database | PostgreSQL (psycopg2) |
| AI | Groq API (Llama 3.3 70B) |
| Frontend | Jinja2, Tailwind CSS, Chart.js |
| PDF Export | ReportLab |
| Email | Flask-Mail, Resend |
| Scheduling | APScheduler |

---
© All Rights Reserved. This repository is provided solely for review purposes. No usage, modification, distribution, or reproduction of this code is permitted without explicit written permission.