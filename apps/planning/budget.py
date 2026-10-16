"""
Deterministic budget planner. PURE Python (no database access) so it is easy to test.

This is a rules-based allocator, NOT artificial intelligence and NOT a price oracle:
  * Category weights below are planning heuristics (relative shares of a ceremony's budget),
    chosen by the operator; they are not market prices.
  * All prices come from vendor packages in the database and keep their price status
    (indicative vs vendor-confirmed). Nothing is invented.
  * All money is integer RWF; rates are integer basis points.

Algorithm summary
  1. spendable = budget / (1 + fee + contingency)   (fee and contingency are added on top of vendor costs)
  2. Split spendable across the chosen ceremonies by CEREMONY_WEIGHTS.
  3. Within a ceremony, split by category shares (priority categories get 1.5x).
  4. For each strategy, pick one eligible package per category, then run a "fit" loop that
     downgrades the choice with the largest saving until the plan fits, recording each trade-off.
"""
from dataclasses import dataclass, field

from apps.core.money import bps_of

# relative weights of each ceremony in the overall budget
CEREMONY_WEIGHTS = {"reception": 40, "gusaba": 20, "traditional": 15, "church": 15, "civil": 10}

# ceremony -> [(category slug, relative share, essential?)]
CEREMONY_RULES = {
    "gusaba": [("tent", 20, True), ("catering", 30, True), ("decoration", 12, False), ("mc", 6, True),
               ("traditional-performers", 9, False), ("sound-lighting", 7, False), ("photographer", 8, False),
               ("transport", 4, False), ("bridal-wear", 4, False)],
    "traditional": [("tent", 18, True), ("catering", 28, True), ("decoration", 12, False), ("mc", 6, True),
                    ("traditional-performers", 12, False), ("sound-lighting", 7, False), ("photographer", 8, False),
                    ("bridal-wear", 9, False)],
    "civil": [("photographer", 30, True), ("transport", 20, True), ("bridal-wear", 20, False), ("makeup", 15, False),
              ("videographer", 15, False)],
    "church": [("photographer", 22, True), ("decoration", 20, False), ("singer", 14, False), ("transport", 14, True),
               ("bridal-wear", 12, False), ("makeup", 8, False), ("videographer", 10, False)],
    "reception": [("venue", 22, True), ("catering", 28, True), ("decoration", 10, False), ("cake", 4, False),
                  ("mc", 4, True), ("dj", 5, False), ("band", 6, False), ("sound-lighting", 6, True),
                  ("photographer", 5, False), ("videographer", 4, False), ("transport", 3, False),
                  ("makeup", 3, False)],
}

STRATEGIES = {
    "budget": ("Budget-friendly", "Essentials only, plus your priorities, choosing the lowest-priced suitable package for each."),
    "balanced": ("Balanced", "Spreads your budget across services by typical share, then fills extras with what is left."),
    "priority": ("Priority-focused", "Puts more of your budget into your priority services and keeps the rest lean."),
}


@dataclass(frozen=True)
class PackageInfo:
    id: int
    vendor_id: int
    vendor_name: str
    name: str
    category_slug: str
    pricing_unit: str  # "flat" | "per_guest"
    price_rwf: int
    min_guests: int
    max_guests: int | None
    ceremony_types: tuple
    districts: frozenset
    price_status: str
    is_price_stale: bool

    def total_for(self, guests: int) -> int:
        return self.price_rwf * guests if self.pricing_unit == "per_guest" else self.price_rwf

    def eligible(self, ceremony_code: str, district: str, guests: int) -> bool:
        return (
            district in self.districts
            and guests >= self.min_guests
            and (self.max_guests is None or guests <= self.max_guests)
            and (not self.ceremony_types or ceremony_code in self.ceremony_types)
        )


@dataclass(frozen=True)
class CeremonyInput:
    code: str
    guests: int
    district: str


@dataclass(frozen=True)
class PlanInput:
    budget_rwf: int
    ceremonies: tuple
    priorities: frozenset = frozenset()
    fee_bps: int = 0
    contingency_bps: int = 0


@dataclass
class OptionItem:
    ceremony: str
    category: str
    package: PackageInfo
    guests: int
    quantity: int
    unit_price_rwf: int
    line_total_rwf: int
    is_priority: bool = False


@dataclass
class PlanOption:
    strategy: str
    label: str
    description: str
    items: list = field(default_factory=list)
    missing: list = field(default_factory=list)      # [(ceremony, category, reason)]
    skipped_optional: list = field(default_factory=list)
    trade_offs: list = field(default_factory=list)
    subtotal_rwf: int = 0
    fee_rwf: int = 0
    contingency_rwf: int = 0
    total_rwf: int = 0
    remaining_rwf: int = 0
    within_budget: bool = True

    def signature(self):
        return tuple(sorted((i.ceremony, i.category, i.package.id) for i in self.items))


def plan_totals(subtotal_rwf: int, fee_bps: int, contingency_bps: int, budget_rwf: int | None = None):
    fee = bps_of(subtotal_rwf, fee_bps)
    contingency = bps_of(subtotal_rwf, contingency_bps)
    total = subtotal_rwf + fee + contingency
    remaining = None if budget_rwf is None else budget_rwf - total
    return {"subtotal": subtotal_rwf, "fee": fee, "contingency": contingency, "total": total, "remaining": remaining}


def spendable_budget(budget_rwf: int, fee_bps: int, contingency_bps: int) -> int:
    """Largest subtotal S such that S + fee(S) + contingency(S) <= budget (integer, conservative)."""
    s = budget_rwf * 10000 // (10000 + fee_bps + contingency_bps)
    while s > 0 and plan_totals(s, fee_bps, contingency_bps)["total"] > budget_rwf:
        s -= 1
    return s


def split_by_weights(total: int, weights: dict) -> dict:
    """Integer split that always sums exactly to `total` (largest-remainder method)."""
    wsum = sum(weights.values())
    if wsum == 0:
        return {k: 0 for k in weights}
    base = {k: total * w // wsum for k, w in weights.items()}
    leftover = total - sum(base.values())
    order = sorted(weights, key=lambda k: ((total * weights[k]) % wsum, weights[k]), reverse=True)
    for k in order[:leftover]:
        base[k] += 1
    return base


def _category_plan(ceremony_code: str, priorities: frozenset, strategy: str):
    """Return (included [(slug, share, essential, is_priority)], skipped_optional [slug])."""
    included, skipped = [], []
    for slug, share, essential in CEREMONY_RULES[ceremony_code]:
        is_priority = slug in priorities
        if essential or is_priority:
            included.append((slug, share * 3 // 2 if is_priority else share, essential, is_priority))
        elif strategy == "balanced":
            included.append((slug, share, False, False))  # optional extras only entered if they fit later
        else:
            skipped.append(slug)
    return included, skipped


def build_option(plan: PlanInput, packages: list, strategy: str) -> PlanOption:
    label, description = STRATEGIES[strategy]
    option = PlanOption(strategy=strategy, label=label, description=description)

    by_category: dict = {}
    for p in packages:
        by_category.setdefault(p.category_slug, []).append(p)

    spendable = spendable_budget(plan.budget_rwf, plan.fee_bps, plan.contingency_bps)
    cer_weights = {c.code: CEREMONY_WEIGHTS.get(c.code, 10) for c in plan.ceremonies}
    cer_alloc = split_by_weights(spendable, cer_weights)

    chosen: dict = {}      # (ceremony, category) -> OptionItem
    optional_extra = []    # balanced-strategy extras: (ceremony, slug, allocation, is_priority)

    for cer in plan.ceremonies:
        included, skipped = _category_plan(cer.code, plan.priorities, strategy)
        option.skipped_optional += [(cer.code, s) for s in skipped]
        alloc = split_by_weights(cer_alloc[cer.code], {slug: share for slug, share, _, _ in included})
        for slug, share, essential, is_priority in included:
            eligible = sorted(
                (p for p in by_category.get(slug, []) if p.eligible(cer.code, cer.district, cer.guests)),
                key=lambda p: (p.total_for(cer.guests), p.id),
            )
            if not eligible:
                if essential or is_priority:
                    option.missing.append((cer.code, slug, "no_matching_vendor"))
                continue
            allocation = alloc[slug]
            if strategy == "budget":
                pick = eligible[0]
            elif strategy == "priority":
                cap = allocation * 3 // 2 if is_priority else 0
                within = [p for p in eligible if p.total_for(cer.guests) <= cap]
                pick = within[-1] if within else eligible[0]
            else:
                within = [p for p in eligible if p.total_for(cer.guests) <= allocation]
                pick = within[-1] if within else eligible[0]
            if not (essential or is_priority):
                optional_extra.append((cer.code, slug, pick, cer, is_priority))
                continue
            chosen[(cer.code, slug)] = _make_item(cer, slug, pick, is_priority)

    subtotal = sum(i.line_total_rwf for i in chosen.values())

    # Fit loop: downgrade the choice with the biggest saving until the plan fits.
    def alternatives(item):
        cer = next(c for c in plan.ceremonies if c.code == item.ceremony)
        cheaper = sorted(
            (p for p in by_category[item.category] if p.eligible(cer.code, cer.district, cer.guests)
             and p.total_for(cer.guests) < item.line_total_rwf),
            key=lambda p: (-p.total_for(cer.guests), p.id),
        )
        return cer, cheaper

    while subtotal > spendable:
        best = None
        for pass_priority in (False, True):
            for key, item in chosen.items():
                if item.is_priority != pass_priority:
                    continue
                cer, cheaper = alternatives(item)
                if not cheaper:
                    continue
                # largest saving = go to the cheapest available (so the loop terminates quickly)
                target = cheaper[-1]
                saving = item.line_total_rwf - target.total_for(cer.guests)
                if best is None or saving > best[0]:
                    best = (saving, key, cer, target, item)
            if best:
                break
        if not best:
            break
        saving, key, cer, target, old = best
        new_item = _make_item(cer, key[1], target, old.is_priority)
        option.trade_offs.append(
            f"Chose '{target.name}' ({target.vendor_name}) instead of '{old.package.name}' for "
            f"{key[1].replace('-', ' ')} at the {key[0]} to save RWF {saving:,}."
        )
        chosen[key] = new_item
        subtotal -= saving

    # Optional extras (balanced strategy): add cheapest-first only if they still fit.
    for code, slug, pick, cer, is_priority in sorted(optional_extra, key=lambda e: e[2].total_for(e[3].guests)):
        cost = pick.total_for(cer.guests)
        if subtotal + cost <= spendable:
            chosen[(code, slug)] = _make_item(cer, slug, pick, is_priority)
            subtotal += cost
        else:
            option.skipped_optional.append((code, slug))

    order = {c.code: n for n, c in enumerate(plan.ceremonies)}
    option.items = sorted(chosen.values(), key=lambda i: (order[i.ceremony], i.category))
    totals = plan_totals(sum(i.line_total_rwf for i in option.items), plan.fee_bps, plan.contingency_bps, plan.budget_rwf)
    option.subtotal_rwf, option.fee_rwf = totals["subtotal"], totals["fee"]
    option.contingency_rwf, option.total_rwf, option.remaining_rwf = totals["contingency"], totals["total"], totals["remaining"]
    option.within_budget = option.remaining_rwf >= 0
    if not option.within_budget:
        over = -option.remaining_rwf
        option.trade_offs.append(
            f"Even the lowest-priced matching options come to RWF {over:,} over your budget. "
            "You could reduce the guest count, drop a ceremony or optional service, or increase the budget."
        )
    return option


def _make_item(cer: CeremonyInput, slug: str, pkg: PackageInfo, is_priority: bool) -> OptionItem:
    qty = cer.guests if pkg.pricing_unit == "per_guest" else 1
    return OptionItem(cer.code, slug, pkg, cer.guests, qty, pkg.price_rwf, pkg.total_for(cer.guests), is_priority)


def build_options(plan: PlanInput, packages: list) -> list:
    """Return distinct plan options (alternative combinations), cheapest strategy first."""
    options, seen = [], set()
    for strategy in ("budget", "balanced", "priority"):
        option = build_option(plan, packages, strategy)
        sig = option.signature()
        if sig in seen:
            continue
        seen.add(sig)
        options.append(option)
    return options
