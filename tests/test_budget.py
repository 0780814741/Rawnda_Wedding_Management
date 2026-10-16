from django.test import SimpleTestCase

from apps.core.money import bps_of, format_rwf
from apps.planning import budget as b


def pkg(id, cat, price, unit="flat", ming=0, maxg=None, districts=("gasabo",), cer=(), status="indicative", vendor=None):
    return b.PackageInfo(id=id, vendor_id=vendor or id, vendor_name=f"V{vendor or id}", name=f"P{id}", category_slug=cat,
                         pricing_unit=unit, price_rwf=price, min_guests=ming, max_guests=maxg, ceremony_types=tuple(cer),
                         districts=frozenset(districts), price_status=status, is_price_stale=False)


def plan(budget=10_000_000, guests=100, ceremonies=("reception",), priorities=(), fee=500, cont=1000):
    return b.PlanInput(budget, tuple(b.CeremonyInput(c, guests, "gasabo") for c in ceremonies), frozenset(priorities), fee, cont)


RECEPTION = [pkg(1, "venue", 1_000_000), pkg(2, "venue", 3_000_000), pkg(3, "catering", 5_000, "per_guest"),
             pkg(4, "catering", 12_000, "per_guest"), pkg(5, "mc", 100_000), pkg(6, "sound-lighting", 200_000)]


class MoneyTests(SimpleTestCase):
    def test_bps_rounds_half_up_with_integers(self):
        self.assertEqual(bps_of(1_000_000, 500), 50_000)
        self.assertEqual(bps_of(1, 5000), 1)      # 0.5 rounds up
        self.assertEqual(bps_of(3, 1000), 0)      # 0.3 rounds down
        self.assertIsInstance(bps_of(999_999, 333), int)

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            bps_of(-1, 100)

    def test_format(self):
        self.assertEqual(format_rwf(1234567), "RWF 1,234,567")


class AllocationTests(SimpleTestCase):
    def test_split_sums_exactly(self):
        for total in (0, 1, 7, 999_999, 10_000_001):
            parts = b.split_by_weights(total, {"a": 3, "b": 5, "c": 7})
            self.assertEqual(sum(parts.values()), total)

    def test_spendable_leaves_room_for_fee_and_contingency(self):
        s = b.spendable_budget(10_000_000, 500, 1000)
        t = b.plan_totals(s, 500, 1000)["total"]
        self.assertLessEqual(t, 10_000_000)
        self.assertGreater(b.plan_totals(s + 1000, 500, 1000)["total"], 10_000_000)  # and not needlessly low

    def test_totals_remaining(self):
        t = b.plan_totals(1_000_000, 500, 1000, 2_000_000)
        self.assertEqual((t["fee"], t["contingency"], t["total"], t["remaining"]), (50_000, 100_000, 1_150_000, 850_000))


class PlannerTests(SimpleTestCase):
    def test_options_stay_within_generous_budget(self):
        for o in b.build_options(plan(), RECEPTION):
            self.assertTrue(o.within_budget, o.label)
            self.assertEqual(o.subtotal_rwf, sum(i.line_total_rwf for i in o.items))
            self.assertEqual(o.total_rwf, o.subtotal_rwf + o.fee_rwf + o.contingency_rwf)
            self.assertEqual(o.remaining_rwf, 10_000_000 - o.total_rwf)

    def test_per_guest_pricing(self):
        o = b.build_option(plan(guests=100), RECEPTION, "budget")
        cat = next(i for i in o.items if i.category == "catering")
        self.assertEqual((cat.quantity, cat.unit_price_rwf, cat.line_total_rwf), (100, 5_000, 500_000))

    def test_budget_strategy_picks_cheapest(self):
        o = b.build_option(plan(), RECEPTION, "budget")
        self.assertEqual(next(i for i in o.items if i.category == "venue").package.id, 1)

    def test_tight_budget_downgrades_and_explains(self):
        o = b.build_option(plan(budget=4_000_000), RECEPTION, "balanced")
        self.assertTrue(o.within_budget)
        self.assertTrue(any("instead of" in t for t in o.trade_offs) or all(i.package.id in (1, 3, 5, 6) for i in o.items))

    def test_impossible_budget_reports_overrun_not_fake_success(self):
        o = b.build_option(plan(budget=500_000), RECEPTION, "budget")
        self.assertFalse(o.within_budget)
        self.assertLess(o.remaining_rwf, 0)
        self.assertTrue(any("over your budget" in t for t in o.trade_offs))

    def test_missing_vendor_is_reported_not_invented(self):
        o = b.build_option(plan(), [p for p in RECEPTION if p.category_slug != "mc"], "budget")
        self.assertIn(("reception", "mc", "no_matching_vendor"), o.missing)
        self.assertFalse(any(i.category == "mc" for i in o.items))

    def test_eligibility_district_capacity_ceremony(self):
        far = pkg(10, "venue", 100, districts=("musanze",))
        small = pkg(11, "venue", 100, maxg=50)
        wrong = pkg(12, "venue", 100, cer=("church",))
        o = b.build_option(plan(), [far, small, wrong] + RECEPTION[2:], "budget")
        self.assertIn(("reception", "venue", "no_matching_vendor"), o.missing)

    def test_priority_gets_more_budget(self):
        base = b.build_option(plan(), RECEPTION, "priority")
        pri = b.build_option(plan(priorities=("venue",)), RECEPTION, "priority")
        v = lambda o: next(i for i in o.items if i.category == "venue").line_total_rwf
        self.assertGreaterEqual(v(pri), v(base))

    def test_deterministic(self):
        a = [o.signature() for o in b.build_options(plan(), RECEPTION)]
        c = [o.signature() for o in b.build_options(plan(), RECEPTION)]
        self.assertEqual(a, c)

    def test_no_floats_in_results(self):
        for o in b.build_options(plan(), RECEPTION):
            for v in (o.subtotal_rwf, o.fee_rwf, o.contingency_rwf, o.total_rwf, o.remaining_rwf):
                self.assertIsInstance(v, int)

    def test_multiple_ceremonies(self):
        pk = RECEPTION + [pkg(20, "tent", 600_000), pkg(21, "catering", 6_000, "per_guest", cer=("gusaba",)), pkg(22, "mc", 90_000, cer=("gusaba",))]
        o = b.build_option(plan(ceremonies=("gusaba", "reception"), budget=15_000_000), pk, "balanced")
        self.assertEqual({i.ceremony for i in o.items}, {"gusaba", "reception"})
