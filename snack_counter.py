"""Core logic for Framsikt's Snack Counter Automation assignment.

Amounts are represented as integer minor units (øre/paise) to avoid
floating-point rounding errors. The implementation is deliberately small
and dependency-free; the in-memory stores stand in for production services.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from threading import RLock
from typing import Callable
from uuid import uuid4


@dataclass(frozen=True)
class Office:
    name: str
    currency: str
    monthly_budget_minor: int


@dataclass
class Employee:
    employee_id: str
    name: str
    payroll_currency: str
    offices: set[str]
    available: bool = True


@dataclass
class Refill:
    refill_id: str
    office: str
    item: str
    quantity: int
    status: str = "OPEN"
    assigned_to: str | None = None
    assigned_at: datetime | None = None
    deadline_at: datetime | None = None
    purchase_at: datetime | None = None
    amount_minor: int | None = None
    purchase_currency: str | None = None
    receipt_image: str | None = None
    hr_claim_id: str | None = None


class SimulatedChat:
    """A stand-in for the company's internal chat tool."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    def send(self, recipient: str, message: str) -> None:
        self.messages.append({"recipient": recipient, "message": message})


class SimulatedHR:
    """A stand-in for the external HR reimbursement system."""

    def __init__(self) -> None:
        self.claims: dict[str, dict] = {}

    def create_claim(
        self, employee_id: str, amount_minor: int, currency: str,
        description: str, receipt_image: str
    ) -> str:
        claim_id = f"HR-{uuid4().hex[:10].upper()}"
        self.claims[claim_id] = {
            "employee_id": employee_id,
            "amount_minor": amount_minor,
            "currency": currency,
            "description": description,
            "receipt_image": receipt_image,
            "status": "PENDING",
        }
        return claim_id

    def approve_claim(self, claim_id: str) -> None:
        if claim_id not in self.claims:
            raise KeyError(f"Unknown HR claim: {claim_id}")
        self.claims[claim_id]["status"] = "APPROVED"


class SnackCounterService:
    ACTIVE_SPEND_STATUSES = {"PURCHASED", "SUBMITTED", "REIMBURSED"}

    def __init__(
        self,
        offices: dict[str, Office],
        employees: list[Employee],
        exchange_rates: dict[tuple[str, str], Decimal | str | float] | None = None,
        assignment_ttl: timedelta = timedelta(hours=24),
        clock: Callable[[], datetime] | None = None,
        chat: SimulatedChat | None = None,
        hr: SimulatedHR | None = None,
    ) -> None:
        self.offices = offices
        self.employees = {e.employee_id: e for e in employees}
        self.exchange_rates = {
            pair: Decimal(str(rate))
            for pair, rate in (exchange_rates or {}).items()
        }
        self.assignment_ttl = assignment_ttl
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.chat = chat or SimulatedChat()
        self.hr = hr or SimulatedHR()
        self.refills: dict[str, Refill] = {}
        self._lock = RLock()
        self._last_assigned: dict[tuple[str, str], datetime] = {}
        self._assignment_count: dict[tuple[str, str], int] = {}

    def create_refill(self, office: str, item: str, quantity: int = 1) -> Refill:
        if office not in self.offices:
            raise ValueError(f"Unknown office: {office}")
        if quantity < 1:
            raise ValueError("Quantity must be at least one")
        refill = Refill(uuid4().hex, office, item, quantity)
        with self._lock:
            self.refills[refill.refill_id] = refill
        return refill

    def _eligible(self, office: str) -> list[Employee]:
        return [
            e for e in self.employees.values()
            if e.available and office in e.offices
        ]

    def choose_volunteer(self, office: str) -> Employee | None:
        """Pick the least-recently assigned available volunteer for this office."""
        candidates = self._eligible(office)
        if not candidates:
            return None

        def fairness_key(employee: Employee):
            key = (office, employee.employee_id)
            last = self._last_assigned.get(key)
            return (
                last or datetime.min.replace(tzinfo=timezone.utc),
                self._assignment_count.get(key, 0),
                employee.employee_id,
            )

        return min(candidates, key=fairness_key)

    def _assign(self, refill: Refill, employee: Employee) -> Refill:
        now = self.clock()
        refill.status = "ASSIGNED"
        refill.assigned_to = employee.employee_id
        refill.assigned_at = now
        refill.deadline_at = now + self.assignment_ttl
        key = (refill.office, employee.employee_id)
        self._last_assigned[key] = now
        self._assignment_count[key] = self._assignment_count.get(key, 0) + 1
        self.chat.send(
            employee.employee_id,
            f"Please refill {refill.item} (quantity {refill.quantity}) "
            f"at {refill.office}. Complete by {refill.deadline_at.isoformat()}.",
        )
        return refill

    def assign_next(self, refill_id: str) -> Refill | None:
        """Assign an OPEN refill, or reassign it if its current assignment expired."""
        with self._lock:
            refill = self.refills[refill_id]
            now = self.clock()
            if refill.status == "ASSIGNED":
                if refill.deadline_at and now < refill.deadline_at:
                    return refill
                refill.status = "OPEN"
                refill.assigned_to = None
            if refill.status != "OPEN":
                return refill
            employee = self.choose_volunteer(refill.office)
            if employee is None:
                return None
            return self._assign(refill, employee)

    def claim_refill(self, refill_id: str, employee_id: str) -> bool:
        """Atomically let a volunteer claim an OPEN refill; first successful claim wins."""
        with self._lock:
            refill = self.refills[refill_id]
            employee = self.employees[employee_id]
            now = self.clock()
            if refill.status == "ASSIGNED" and refill.deadline_at and now >= refill.deadline_at:
                refill.status = "OPEN"
                refill.assigned_to = None
            if refill.status != "OPEN":
                return False
            if not employee.available or refill.office not in employee.offices:
                return False
            self._assign(refill, employee)
            return True

    def reassign_expired(self) -> list[Refill]:
        reassigned = []
        with self._lock:
            expired_ids = [
                r.refill_id for r in self.refills.values()
                if r.status == "ASSIGNED" and r.deadline_at and self.clock() >= r.deadline_at
            ]
            for refill_id in expired_ids:
                refill = self.refills[refill_id]
                refill.status = "OPEN"
                refill.assigned_to = None
                updated = self.assign_next(refill_id)
                if updated and updated.status == "ASSIGNED":
                    reassigned.append(updated)
        return reassigned

    def _convert_minor(self, amount_minor: int, source: str, target: str) -> int:
        if source == target:
            return amount_minor
        rate = self.exchange_rates.get((source, target))
        if rate is None:
            raise ValueError(f"Missing exchange rate: {source} -> {target}")
        return int(
            (Decimal(amount_minor) * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )

    def _spent_minor(self, office: str, month: str) -> int:
        return sum(
            r.amount_minor or 0
            for r in self.refills.values()
            if r.office == office
            and r.status in self.ACTIVE_SPEND_STATUSES
            and r.purchase_at is not None
            and r.purchase_at.strftime("%Y-%m") == month
        )

    def complete_refill(
        self, refill_id: str, employee_id: str, amount_minor: int,
        receipt_image: str, purchase_at: datetime | None = None
    ) -> str:
        """Record the purchase and submit an HR claim. Returns the HR claim ID."""
        if amount_minor <= 0:
            raise ValueError("Purchase amount must be positive")
        if not receipt_image:
            raise ValueError("A receipt image is required")

        with self._lock:
            refill = self.refills[refill_id]
            now = self.clock()
            if refill.status != "ASSIGNED" or refill.assigned_to != employee_id:
                raise ValueError("Only the assigned volunteer can complete this refill")
            if refill.deadline_at and now >= refill.deadline_at:
                raise ValueError("Assignment has expired; request reassignment")

            office = self.offices[refill.office]
            employee = self.employees[employee_id]
            purchase_time = purchase_at or now
            month = purchase_time.strftime("%Y-%m")
            spent = self._spent_minor(refill.office, month)
            if spent + amount_minor > office.monthly_budget_minor:
                raise ValueError(
                    f"Monthly budget exceeded for {refill.office}: "
                    f"{spent + amount_minor} > {office.monthly_budget_minor} "
                    f"{office.currency} minor units"
                )

            # Resolve conversion before mutating state so a missing rate cannot
            # leave a purchase recorded without a corresponding HR claim.
            payroll_amount = self._convert_minor(
                amount_minor, office.currency, employee.payroll_currency
            )
            claim_id = self.hr.create_claim(
                employee_id=employee_id,
                amount_minor=payroll_amount,
                currency=employee.payroll_currency,
                description=f"Snack counter refill: {refill.item} at {refill.office}",
                receipt_image=receipt_image,
            )

            refill.status = "SUBMITTED"
            refill.purchase_at = purchase_time
            refill.amount_minor = amount_minor
            refill.purchase_currency = office.currency
            refill.receipt_image = receipt_image
            refill.hr_claim_id = claim_id
            self.chat.send(
                employee_id,
                f"Your reimbursement claim {claim_id} was submitted to HR.",
            )
            return claim_id

    def mark_reimbursed(self, refill_id: str) -> None:
        with self._lock:
            refill = self.refills[refill_id]
            if refill.status != "SUBMITTED" or not refill.hr_claim_id:
                raise ValueError("Only submitted refills can be marked reimbursed")
            self.hr.approve_claim(refill.hr_claim_id)
            refill.status = "REIMBURSED"

    def monthly_report(self, month: str, reporting_currency: str | None = None) -> dict:
        """Return office totals and a safe currency-bucketed cross-office total.

        An optional reporting currency produces a converted consolidated total;
        rates must be explicitly configured rather than silently assumed.
        """
        with self._lock:
            offices = {}
            totals_by_currency: dict[str, int] = {}
            converted_total = 0
            conversion_complete = reporting_currency is not None

            for office_name, office in self.offices.items():
                spent = self._spent_minor(office_name, month)
                offices[office_name] = {
                    "currency": office.currency,
                    "spent_minor": spent,
                    "budget_minor": office.monthly_budget_minor,
                    "remaining_minor": office.monthly_budget_minor - spent,
                }
                totals_by_currency[office.currency] = (
                    totals_by_currency.get(office.currency, 0) + spent
                )
                if reporting_currency:
                    try:
                        converted_total += self._convert_minor(
                            spent, office.currency, reporting_currency
                        )
                    except ValueError:
                        conversion_complete = False

            result = {
                "month": month,
                "offices": offices,
                "totals_by_currency": totals_by_currency,
            }
            if reporting_currency and conversion_complete:
                result["reporting_currency"] = reporting_currency
                result["converted_total_minor"] = converted_total
            elif reporting_currency:
                result["conversion_note"] = (
                    f"Add exchange rates for every office currency to {reporting_currency} "
                    "to calculate a single consolidated figure."
                )
            return result


def default_service(clock=None) -> SnackCounterService:
    offices = {
        "Oslo": Office("Oslo", "NOK", 600_000),       # 6,000.00 NOK
        "Bengaluru": Office("Bengaluru", "INR", 1_500_000),  # 15,000.00 INR
    }
    employees = [
        Employee("NO-01", "Oslo volunteer 1", "NOK", {"Oslo"}),
        Employee("NO-02", "Oslo volunteer 2", "NOK", {"Oslo"}),
        Employee("IN-01", "Bengaluru volunteer 1", "INR", {"Bengaluru"}),
        Employee("IN-02", "Bengaluru volunteer 2", "INR", {"Bengaluru"}),
        # A Bengaluru employee temporarily visiting Oslo.
        Employee("IN-VISITOR", "Visiting colleague", "INR", {"Bengaluru", "Oslo"}),
    ]
    return SnackCounterService(
        offices=offices,
        employees=employees,
        exchange_rates={("NOK", "INR"): Decimal("8.50"), ("INR", "NOK"): Decimal("0.118")},
        clock=clock,
    )


if __name__ == "__main__":
    service = default_service()
    refill = service.create_refill("Bengaluru", "Coffee", 2)
    assigned = service.assign_next(refill.refill_id)
    if assigned:
        claim = service.complete_refill(
            refill.refill_id, assigned.assigned_to, 125_000, "receipts/coffee.jpg"
        )
        print(f"Assigned to: {assigned.assigned_to}")
        print(f"HR claim: {claim}")
        print(service.monthly_report(service.clock().strftime("%Y-%m")))
