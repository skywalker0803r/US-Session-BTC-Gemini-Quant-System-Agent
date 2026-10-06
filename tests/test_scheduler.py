from datetime import datetime, timezone

from quant_agent.cli import main
from quant_agent.scheduler import is_close_window, is_market_open


def test_market_window_respects_dst_and_new_york_timezone():
    summer_open = datetime.fromisoformat("2026-07-01T13:30:00+00:00")
    winter_open = datetime.fromisoformat("2026-01-15T14:30:00+00:00")

    assert is_market_open(summer_open)
    assert is_market_open(winter_open)
    assert not is_market_open(datetime.fromisoformat("2026-07-01T21:00:00+00:00"))
    assert not is_close_window(datetime.fromisoformat("2026-07-01T19:00:00+00:00"))


def test_cli_skips_trade_outside_market_window(tmp_path, capsys):
    result = main([
        "--mode", "simulate",
        "--action", "open",
        "--events", str(tmp_path),
        "--now", "2026-07-01T21:00:00+00:00",
    ])

    assert result == 0
    output = capsys.readouterr().out
    assert "SKIPPED" in output
    assert "Outside America/New_York market open window" in output


def test_cli_skips_close_outside_close_window(tmp_path, capsys):
    result = main([
        "--mode", "simulate",
        "--action", "close",
        "--events", str(tmp_path),
        "--now", "2026-07-01T19:00:00+00:00",
    ])

    assert result == 0
    output = capsys.readouterr().out
    assert "SKIPPED" in output
    assert "Outside America/New_York close window" in output
