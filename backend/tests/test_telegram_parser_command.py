from pytest import CaptureFixture

from app.telegram.test_parser import main


def test_manual_parser_command(capsys: CaptureFixture[str]) -> None:
    main()

    output = capsys.readouterr().out
    assert "recipient_tag: Stephen_Mckinney_21" in output
    assert "amount: 36.28" in output
    assert "payment_sender_name: Krista R" in output
    assert "payment_datetime: 2026-06-29 15:08:00" in output
    assert "total_in: 5709.59" in output
    assert "total_out: 1881.66" in output
