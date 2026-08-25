"""Iteration 19 — unit-level checks on the fixed matcher + parser helpers."""
import sys

sys.path.insert(0, "/app/backend")
from statements import _auto_map_columns, _match_score, parse_csv  # noqa: E402


def _b(desc, amt=500, date="10 Sep 2026", t="Expense"):
    return {"id": "b", "source": "bank", "type": t, "amount": amt, "date": date, "description": desc}


def _u(desc, amt=500, date="10 Sep 2026", t="Expense", **kw):
    d = {"id": "u", "source": "upi", "type": t, "amount": amt, "date": date, "description": desc}
    d.update(kw)
    return d


def test_generic_pair_scores_in_possible_band():
    score, reason = _match_score(_b("Paid Wwww Alpha", 8765),
                                 _u("Paid Yyyy Beta", 8765, date="12 Sep 2026", merchant="Yyyy Beta"))
    assert 0.6 <= score < 0.85, f"score {score} ({reason}) outside possible band"


def test_merchant_match_with_ref_auto_dedupes():
    score, _ = _match_score(_b("UPI/SWIGGY ORDER BLR/778899001122", 450),
                            _u("Swiggy", 450, merchant="Swiggy", upi_ref="778899001122"))
    assert score >= 0.85, score


def test_merchant_from_description_when_no_merchant_column():
    score, _ = _match_score(_b("UPI/SWIGGY ORDER BLR/778899001122", 450),
                            _u("Swiggy", 450, upi_ref="778899001122"))
    assert score >= 0.85, score


def test_conflicting_refs_veto():
    score, reason = _match_score(_b("UPI/ACME/111111111111", 1250),
                                 _u("ACME", 1250, date="12 Sep 2026", merchant="ACME",
                                    upi_ref="222222222222"))
    assert score == 0.0 and "mismatch" in reason, (score, reason)


def test_txn_id_conflict_also_vetoes():
    score, reason = _match_score(_b("UPI/ACME/111111111111", 1250),
                                 _u("ACME", 1250, merchant="ACME", txn_id="333333333333"))
    assert score == 0.0, (score, reason)


def test_bank_ref_only_still_matches_merchant():
    """Only the bank side carries a ref -> no veto, merchant match still merges."""
    score, _ = _match_score(_b("UPI/ZOMATO/111111111111", 700),
                            _u("Zomato", 700, merchant="Zomato"))
    assert score >= 0.85, score


def test_short_alias_word_boundary():
    g = _auto_map_columns(["Date", "Description", "Debit", "Balance"])
    assert g["credit"] != "Description", g
    assert g["debit"] == "Debit", g
    g2 = _auto_map_columns(["Txn Date", "Particulars", "DR", "CR"])
    assert (g2["debit"], g2["credit"]) == ("DR", "CR"), g2
    g3 = _auto_map_columns(["Date", "Narration", "Dr Amount", "Cr Amount"])
    assert (g3["debit"], g3["credit"]) == ("Dr Amount", "Cr Amount"), g3


def test_bank_amount_only_direction_and_income_keywords():
    csv = (
        "Date,Description,Amount\n"
        "01/09/2025,SWIGGY ORDER BLR,450\n"
        "02/09/2025,BIGBASKET GROCERY,2200\n"
        "03/09/2025,Refund from Amazon,900\n"
        "04/09/2025,Interest credit,120\n"
        "05/09/2025,Salary from Acme,80000\n"
    ).encode()
    mapping = {"date": "Date", "description": "Description", "amount": "Amount"}
    txns = {t["description"]: t["type"] for t in parse_csv(csv, mapping, source="bank")}
    assert txns["SWIGGY ORDER BLR"] == "Expense", txns
    assert txns["BIGBASKET GROCERY"] == "Expense", txns
    assert txns["Salary from Acme"] == "Income", txns
    assert txns["Refund from Amazon"] == "Income", txns
    assert txns["Interest credit"] == "Income", txns


def test_negative_amount_still_expense_and_explicit_type_wins():
    csv = ("Date,Description,Amount,Type\n"
           "01/09/2025,SOME SHOP,-450,DR\n"
           "02/09/2025,ACME PAYOUT,1500,CR\n").encode()
    mapping = {"date": "Date", "description": "Description", "amount": "Amount", "type": "Type"}
    out = {t["description"]: t["type"] for t in parse_csv(csv, mapping, source="bank")}
    assert out["SOME SHOP"] == "Expense", out
    assert out["ACME PAYOUT"] == "Income", out
