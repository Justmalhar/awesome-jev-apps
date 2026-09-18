"""The module under audit. Bundled sample input for test_gap.py.

Read as text and parsed with `ast`; never imported.
"""

from decimal import Decimal


class Order:
    """A customer order and the money arithmetic around it."""

    def __init__(self, lines, currency="GBP"):
        self.lines = lines
        self.currency = currency
        self.refunded = Decimal("0")

    def subtotal(self):
        """Sum of the line totals, before discount and tax."""
        return sum((line.price * line.quantity for line in self.lines), Decimal("0"))

    def apply_discount(self, code, percent):
        """Reduce the subtotal by `percent`, before tax is computed."""
        if not 0 <= percent <= 100:
            raise ValueError("percent must be between 0 and 100")
        return self.subtotal() * (Decimal(100 - percent) / 100)

    def tax(self, rate):
        """VAT on the discounted subtotal, rounded to the currency's minor unit."""
        return (self.subtotal() * Decimal(rate)).quantize(Decimal("0.01"))

    def refund(self, amount, reason):
        """Refund `amount` to the customer. Cannot exceed what remains refundable."""
        if amount <= 0:
            raise ValueError("refund must be positive")
        if self.refunded + amount > self.subtotal():
            raise ValueError("refund exceeds the order total")
        self.refunded += amount
        return {"refunded": self.refunded, "reason": reason}

    def describe(self):
        """Human-readable one-liner for the admin UI."""
        return f"{len(self.lines)} line(s) in {self.currency}"


def authorise(user, order):
    """Decide whether `user` may act on `order`. Returns True or raises."""
    if user.id != order.owner_id and "admin" not in user.roles:
        raise PermissionError("not your order")
    return True


def parse_currency(text):
    """Turn '12.34' into a Decimal, rejecting anything that is not money."""
    cleaned = text.strip().replace(",", "")
    try:
        return Decimal(cleaned)
    except ArithmeticError as exc:
        raise ValueError(f"not a currency amount: {text!r}") from exc


def format_receipt(order, total):
    """Render the receipt body sent to the customer."""
    return f"{order.describe()}\nTotal: {total} {order.currency}"
