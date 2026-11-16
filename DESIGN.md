# Snack Counter Automation — Design

## 1. Proposed workflow

1. **Report an empty box.** An employee scans a QR code or opens a small internal form, selects the office, snack and quantity, and creates a refill request. The request starts as `OPEN`.
2. **Select and notify a volunteer.** The scheduler chooses an available volunteer who supports that office. Selection is fair: volunteers with the oldest/lowest assignment history are preferred. A volunteer can also claim an open request. An atomic state transition ensures only one person can own it. Chat sends the assignment and deadline.
3. **Purchase and submit.** The assigned volunteer buys the items, then submits the amount and receipt image through the request. The system checks the office's monthly budget before recording the purchase.
4. **Create the HR claim.** The system submits employee ID, amount, payroll currency, description and receipt image to the external HR system. If the employee's payroll currency differs from the office currency, the system uses a configured exchange rate and records the rate used. The refill becomes `SUBMITTED`.
5. **Close the loop.** The HR integration receives an approval/status update and marks the refill `REIMBURSED`. If the volunteer does not complete the task by the deadline, the request returns to `OPEN` and is offered to the next eligible volunteer. The original assignee is notified.

## 2. Main components and data

| Component | Responsibility | Data retained |
|---|---|---|
| Employee directory | Uses company login identity and office eligibility | Employee ID, name, payroll currency, offices they can support, availability |
| Refill service | Owns request lifecycle and prevents duplicate claims | Refill ID, office, item, quantity, status, assignee, timestamps, deadline |
| Assignment scheduler | Chooses volunteers and handles expiry | Assignment history, availability, per-office fairness history |
| Chat adapter | Sends actionable notifications | Recipient, message, delivery reference/status |
| Budget/reporting service | Checks office budgets and produces monthly totals | Office currency/budget, purchase amount, purchase date, reporting month |
| HR adapter | Creates and tracks reimbursement claims | HR claim ID, employee ID, payroll-currency amount, receipt reference, status |
| Receipt storage | Stores proof of purchase securely | Receipt object reference, uploader, timestamp, access metadata |

The assignment's monthly budgets are **6,000 NOK for Oslo** and **15,000 INR for Bengaluru**. Purchases are recorded in the office's local currency for budget control. HR claims are submitted in the employee's payroll currency. The implementation stores money as integer minor units to avoid floating-point errors.

## 3. How the design addresses the current problems

- **Empty boxes:** Every request has an owner, deadline and visible status. Expired assignments are automatically reassigned and escalated in chat if nobody is available.
- **Duplicate purchases:** A request can have only one active assignee. Claiming is an atomic operation; a second volunteer cannot win the same request.
- **Unequal volunteer workload:** The scheduler uses assignment history and availability rather than relying on whoever happens to see a message. Employees temporarily visiting another office can be eligible there.
- **Slow/unclear reimbursement:** The volunteer can see `SUBMITTED` versus `REIMBURSED`, with an HR claim reference. HR approval remains external and can take the stated 1–10 days; the automation does not pretend to control that approval time.
- **Unknown spend and consumption:** Every purchase is tied to an office, item, quantity, date and amount. Monthly reporting gives office totals, budget utilization and totals grouped by currency. A single cross-office numeric total is shown only when a reporting currency and explicit exchange rates are configured.

## 4. Trade-offs and deliberate non-goals

**MVP choices:** Use the existing company login and chat, a lightweight request interface, and an adapter to the HR system. Keep office budgets in local currencies. Make assignment and purchase recording transactional. Use a configurable assignment deadline (24 hours in the demo).

**Currency trade-off:** NOK and INR must not be added as if they were the same unit. The default report shows both currency buckets. Management can choose a reporting currency; then the system converts using a dated, auditable FX rate. The demo uses configured rates, not a live FX feed.

**What I would not build initially:** A full inventory/warehouse system, automatic snack demand forecasting, vendor procurement and purchase orders, a new HR/reimbursement platform, a mobile app, or complex approval workflows. These add cost before the basic ownership, budget and reimbursement loop is reliable. A QR-code entry point and a simple web form are sufficient for the first release.

**Production hardening:** Persist state in a transactional database (e.g. PostgreSQL) with a uniqueness constraint for active assignments; use a durable job queue for deadline retries; store receipts in access-controlled object storage; add idempotency keys to HR requests and webhook processing; record an audit log; and monitor failed chat/HR calls. The included implementation uses in-memory stand-ins to keep the core rules easy to run and test.

## 5. Questions I would clarify before implementation

1. Does the HR integration support claim-status webhooks, or must we poll for approval?
2. What is the authoritative source and update frequency for payroll currency and temporary office access?
3. Should budgets be checked against the purchase date or the claim-submission date, and who can approve an exceptional over-budget purchase?
4. Is there a preferred management reporting currency and an approved FX-rate source?
5. What assignment response window and escalation channel should be used when all volunteers are unavailable?
