from __future__ import annotations

from app.parser.payment import parse_payment_message

SAMPLE_PAYMENT_MESSAGE = """Hi Stephen_Mckinney_21,

You received $36.28 from Krista R.

03:08 PM - 29 Jun 2026
Total In: 5709.59$
Total Out: 1881.66$"""


def main() -> None:
    """Parse and print the documented sample payment notification."""
    parsed = parse_payment_message(SAMPLE_PAYMENT_MESSAGE)
    if parsed is None:
        raise SystemExit("Sample message was not recognized as a payment.")

    print("Payment parser result")
    print(f"  recipient_tag: {parsed.recipient_tag}")
    print(f"  amount: {parsed.amount}")
    print(f"  payment_sender_name: {parsed.payment_sender_name}")
    print(f"  payment_datetime: {parsed.payment_datetime.isoformat(sep=' ')}")
    print(f"  total_in: {parsed.total_in}")
    print(f"  total_out: {parsed.total_out}")


if __name__ == "__main__":
    main()
