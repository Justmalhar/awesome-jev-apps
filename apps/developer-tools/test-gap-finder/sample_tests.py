"""The existing test suite. Bundled sample input for test_gap.py.

Read as text and parsed with `ast`; never imported, never run.
"""


def test_subtotal_sums_line_totals():
    order = make_order([(2, "10.00"), (1, "5.50")])
    assert order.subtotal() == Decimal("25.50")


def test_apply_discount_reduces_subtotal():
    order = make_order([(1, "100.00")])
    assert order.apply_discount("SAVE10", 10) == Decimal("90.00")


def test_apply_discount_rejects_out_of_range():
    order = make_order([(1, "100.00")])
    with pytest.raises(ValueError):
        order.apply_discount("BAD", 120)


def test_refund_is_logged():
    """Exercises refund() on the way to checking the audit log."""
    order = make_order([(1, "50.00")])
    order.refund(Decimal("10.00"), "damaged")
    assert audit_log.last().event == "refund"


def test_describe_mentions_the_currency():
    order = make_order([(1, "1.00")], currency="EUR")
    assert "EUR" in order.describe()


def test_parse_currency_handles_thousands_separators():
    assert parse_currency("1,234.56") == Decimal("1234.56")


def test_receipt_contains_the_total():
    order = make_order([(1, "3.00")])
    assert "3.00" in format_receipt(order, Decimal("3.00"))
