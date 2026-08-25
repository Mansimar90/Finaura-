"""Iteration 18 — false-positive risk in the cross-source auto-dedupe.

Two DISTINCT payments to the same merchant for the same amount 2 days apart, where the
bank narration carries UPI ref A and the UPI statement row carries a DIFFERENT ref B.
Conflicting refs prove these are two different transactions, so neither may be dropped.
"""
import sys

sys.path.insert(0, "/app/backend")
from statements import _match_score, dedupe_across_sources  # noqa: E402


BANK = {
    "id": "b1", "source": "bank", "type": "Expense", "amount": 500,
    "date": "10 Sep 2026", "description": "UPI/ZOMATO/111111111111/Food order",
}
UPI = {
    "id": "u1", "source": "upi", "type": "Expense", "amount": 500,
    "date": "12 Sep 2026", "description": "Zomato", "merchant": "ZOMATO",
    "upi_ref": "222222222222",
}


def test_conflicting_upi_refs_not_auto_deduped():
    score, reason = _match_score(BANK, UPI)
    kept = {t["id"] for t in dedupe_across_sources([BANK, UPI])}
    assert kept == {"b1", "u1"}, (
        f"two distinct Zomato payments (bank ref 111111111111 vs UPI ref 222222222222) were "
        f"auto-merged at score={score:.2f} ({reason}); a conflicting UPI ref must veto the match"
    )


def test_generic_shared_token_alone_is_not_enough():
    """A single generic shared token ('paid') must not push unrelated rows to verified."""
    bank = {"id": "b2", "source": "bank", "type": "Expense", "amount": 8765,
            "date": "10 Sep 2026", "description": "Paid Wwww Alpha"}
    upi = {"id": "u2", "source": "upi", "type": "Expense", "amount": 8765,
           "date": "12 Sep 2026", "description": "Paid Yyyy Beta", "merchant": "Yyyy Beta"}
    score, reason = _match_score(bank, upi)
    assert score < 0.85, f"unrelated rows scored {score:.2f} ({reason}) and will be silently deduped"
