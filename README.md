# Framsikt Snack Counter Automation

A small, dependency-free Python implementation of the assignment's core lifecycle.

## Requirements

- Python 3.10+
- No third-party packages

## Run the demo

```bash
python snack_counter.py
```

## Run automated tests

From this directory:

```bash
python -m unittest discover -s tests -v
```

The tests cover:
- Concurrent claims: only one volunteer can claim a refill.
- Expired assignment: the task is reassigned to the next volunteer.
- Independent Oslo and Bengaluru budgets and monthly totals.
- Visitors: reimbursement is submitted in the employee's payroll currency.
- Optional consolidated reporting in a configured currency.
- Only the assigned volunteer can complete a refill.
- HR approval moves a refill from submitted to reimbursed.

## Money and currency conventions

All amounts passed to the service are integer minor units:
- `100_00` means 100.00 in the relevant currency.
- `590_000` means 5,900.00 NOK.
- `1_400_000` means 14,000.00 INR.

The office currency controls budget accounting. The employee payroll currency controls the HR claim. Exchange rates are explicitly configured as `(source_currency, target_currency): target major units per source major unit`; because NOK and INR both use two decimal places, the same ratio applies to minor units.

## Important scope note

The code uses in-memory data structures and simulated Chat/HR adapters, as requested. It demonstrates the business rules, not production persistence or real integrations. `DESIGN.md` describes the production hardening path and open questions.
