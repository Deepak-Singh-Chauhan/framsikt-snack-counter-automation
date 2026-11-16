import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier, Thread

from snack_counter import (
    Employee, Office, SnackCounterService, SimulatedChat, SimulatedHR
)


class FakeClock:
    def __init__(self):
        self.current = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.current

    def advance(self, delta):
        self.current += delta


def make_service():
    clock = FakeClock()
    offices = {
        "Oslo": Office("Oslo", "NOK", 600_000),
        "Bengaluru": Office("Bengaluru", "INR", 1_500_000),
    }
    employees = [
        Employee("A", "Asha", "NOK", {"Oslo"}),
        Employee("B", "Bjørn", "NOK", {"Oslo"}),
        Employee("C", "Chitra", "INR", {"Bengaluru"}),
        Employee("D", "Dev", "INR", {"Bengaluru"}),
        Employee("VIS", "Visitor", "INR", {"Oslo", "Bengaluru"}),
    ]
    chat, hr = SimulatedChat(), SimulatedHR()
    service = SnackCounterService(
        offices, employees,
        exchange_rates={
            ("NOK", "INR"): Decimal("8.50"),
            ("INR", "NOK"): Decimal("0.118"),
        },
        assignment_ttl=timedelta(hours=1),
        clock=clock, chat=chat, hr=hr,
    )
    return service, clock, hr, chat


class SnackCounterTests(unittest.TestCase):
    def test_two_people_cannot_claim_same_refill(self):
        service, _, _, _ = make_service()
        refill = service.create_refill("Oslo", "Oat bars")
        barrier = Barrier(3)
        results = []

        def attempt(employee_id):
            barrier.wait()
            results.append(service.claim_refill(refill.refill_id, employee_id))

        t1 = Thread(target=attempt, args=("A",))
        t2 = Thread(target=attempt, args=("B",))
        t1.start()
        t2.start()
        barrier.wait()
        t1.join()
        t2.join()

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)
        self.assertIn(refill.assigned_to, {"A", "B"})

    def test_expired_refill_is_reassigned_to_another_volunteer(self):
        service, clock, _, _ = make_service()
        refill = service.create_refill("Oslo", "Coffee")
        first = service.assign_next(refill.refill_id)
        self.assertEqual(first.assigned_to, "A")

        clock.advance(timedelta(hours=2))
        reassigned = service.reassign_expired()

        self.assertEqual(len(reassigned), 1)
        self.assertEqual(reassigned[0].assigned_to, "B")
        self.assertEqual(reassigned[0].status, "ASSIGNED")

    def test_budget_is_enforced_per_office_and_month(self):
        service, _, _, _ = make_service()

        oslo = service.create_refill("Oslo", "Tea")
        service.claim_refill(oslo.refill_id, "A")
        service.complete_refill(oslo.refill_id, "A", 590_000, "oslo.jpg")

        another = service.create_refill("Oslo", "Coffee")
        service.claim_refill(another.refill_id, "B")
        with self.assertRaisesRegex(ValueError, "Monthly budget exceeded"):
            service.complete_refill(another.refill_id, "B", 20_000, "coffee.jpg")

        bengaluru = service.create_refill("Bengaluru", "Biscuits")
        service.claim_refill(bengaluru.refill_id, "C")
        service.complete_refill(bengaluru.refill_id, "C", 1_400_000, "biscuits.jpg")

        report = service.monthly_report("2026-11")
        self.assertEqual(report["offices"]["Oslo"]["spent_minor"], 590_000)
        self.assertEqual(report["offices"]["Bengaluru"]["spent_minor"], 1_400_000)
        self.assertEqual(report["totals_by_currency"], {
            "NOK": 590_000, "INR": 1_400_000
        })

    def test_visiting_employee_is_reimbursed_in_payroll_currency(self):
        service, _, hr, _ = make_service()
        refill = service.create_refill("Oslo", "Fruit")
        self.assertTrue(service.claim_refill(refill.refill_id, "VIS"))
        claim_id = service.complete_refill(
            refill.refill_id, "VIS", 1_000, "fruit-receipt.jpg"
        )
        claim = hr.claims[claim_id]
        self.assertEqual(claim["currency"], "INR")
        self.assertEqual(claim["amount_minor"], 8_500)
        self.assertEqual(claim["employee_id"], "VIS")

    def test_report_can_convert_both_offices_to_one_reporting_currency(self):
        service, _, _, _ = make_service()

        oslo = service.create_refill("Oslo", "Tea")
        service.claim_refill(oslo.refill_id, "A")
        service.complete_refill(oslo.refill_id, "A", 10_000, "tea.jpg")

        blr = service.create_refill("Bengaluru", "Coffee")
        service.claim_refill(blr.refill_id, "C")
        service.complete_refill(blr.refill_id, "C", 20_000, "coffee.jpg")

        report = service.monthly_report("2026-11", reporting_currency="INR")
        # 100 NOK * 8.5 + 200 INR = 1,050 INR
        self.assertEqual(report["converted_total_minor"], 105_000)
        self.assertEqual(report["reporting_currency"], "INR")

    def test_only_assigned_volunteer_can_complete_refill(self):
        service, _, _, _ = make_service()
        refill = service.create_refill("Oslo", "Nuts")
        service.claim_refill(refill.refill_id, "A")
        with self.assertRaisesRegex(ValueError, "Only the assigned volunteer"):
            service.complete_refill(refill.refill_id, "B", 10_000, "receipt.jpg")

    def test_approved_claim_moves_refill_to_reimbursed(self):
        service, _, hr, _ = make_service()
        refill = service.create_refill("Bengaluru", "Crackers")
        service.claim_refill(refill.refill_id, "C")
        claim_id = service.complete_refill(
            refill.refill_id, "C", 25_000, "receipt.jpg"
        )
        self.assertEqual(hr.claims[claim_id]["status"], "PENDING")
        service.mark_reimbursed(refill.refill_id)
        self.assertEqual(refill.status, "REIMBURSED")
        self.assertEqual(hr.claims[claim_id]["status"], "APPROVED")


if __name__ == "__main__":
    unittest.main()
