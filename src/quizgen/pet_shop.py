"""
Pet shop — the cosmetics catalog and the points arithmetic behind it.

Points are never stored as a mutable balance that could drift from reality (the same
reasoning docs/q-score.md gives for not storing Q Score itself). They are derived on every
read from two facts that ARE stored: how many distinct trainings this learner has ever been
certified on (dbo.Certificates / bank.certificates — active or expired, since finishing a
training earns the reward permanently even if the certificate later lapses), and which
items they have bought (dbo.PetPurchases / bank.pet_purchases). A balance column would need
to be debited and credited in lockstep with those two facts everywhere they change; deriving
it means there is nothing to keep in sync and nothing that can go stale.

One item per slot may be equipped at a time — buying a second hat does not require taking
off the first, but wearing both does not make sense either.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

POINTS_PER_TRAINING = 100

CATALOG: List[Dict] = [
    {"id": "antenna_bow", "name": "Antenna Bow", "cost": 50, "slot": "head"},
    {"id": "sunglasses", "name": "Sunglasses", "cost": 75, "slot": "eyes"},
    {"id": "bowtie", "name": "Bow Tie", "cost": 75, "slot": "neck"},
    {"id": "scarf", "name": "Scarf", "cost": 120, "slot": "neck"},
    {"id": "jetpack", "name": "Jetpack", "cost": 200, "slot": "back"},
    {"id": "crown", "name": "Crown", "cost": 250, "slot": "head"},
]

CATALOG_BY_ID: Dict[str, Dict] = {item["id"]: item for item in CATALOG}


def catalog_public() -> List[Dict]:
    """A plain copy of CATALOG, safe to hand straight to _json/jsonify."""
    return [dict(item) for item in CATALOG]


def is_valid_item(item_id: str) -> bool:
    return item_id in CATALOG_BY_ID


def points_earned(trainings_completed: int) -> int:
    return max(0, int(trainings_completed)) * POINTS_PER_TRAINING


def points_spent(owned_item_ids: Iterable[str]) -> int:
    return sum(CATALOG_BY_ID[i]["cost"] for i in owned_item_ids if i in CATALOG_BY_ID)


def points_balance(trainings_completed: int, owned_item_ids: Iterable[str]) -> int:
    return points_earned(trainings_completed) - points_spent(owned_item_ids)


def can_afford(trainings_completed: int, owned_item_ids: Iterable[str], item_id: str) -> bool:
    """Whether item_id is a real item, not already owned, and within budget."""
    item = CATALOG_BY_ID.get(item_id)
    if item is None:
        return False
    owned = set(owned_item_ids)
    if item_id in owned:
        return False
    return points_balance(trainings_completed, owned) >= item["cost"]


def slot_of(item_id: str) -> Optional[str]:
    item = CATALOG_BY_ID.get(item_id)
    return item["slot"] if item else None
