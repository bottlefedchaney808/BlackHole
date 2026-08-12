"""Unit tests for rh_monitor.evaluate_position (pure alert logic, no network)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rh_monitor as rm  # noqa: E402


def test_far_above_stop_no_alert():
    # SOUN 7.42 avg, stop 6.40 -> last 7.35 is +14.8% above stop, below scale -> no alert
    line, alert = rm.evaluate_position("SOUN", 7.35, 7.42, 6.40, 8.00)
    assert "7.35" in line and "/6.40" in line
    assert alert is None


def test_within_2pct_of_stop_alerts():
    # TGB avg 8.86 stop 7.50: last 7.62 = +1.6% above stop -> within 2% band -> alert
    line, alert = rm.evaluate_position("TGB", 7.62, 8.86, 7.50, 9.60)
    assert alert is not None and "stop" in alert and "TGB" in alert


def test_at_or_below_stop_alerts():
    # UUUU stop 13.00: last 12.95 below stop -> alert
    line, alert = rm.evaluate_position("UUUU", 12.95, 14.17, 13.00, 15.30)
    assert alert is not None and "below stop" in alert


def test_scale_out_plus8_alerts():
    # UUUU avg 14.17: last 15.40 = +8.7% -> scale-out alert
    line, alert = rm.evaluate_position("UUUU", 15.40, 14.17, 13.00, 15.30)
    assert alert is not None and "scale-out" in alert


def test_missing_quote_alerts():
    line, alert = rm.evaluate_position("KOS", None, 2.57, 2.20, 3.00)
    assert alert is not None and "no quote" in alert


def test_known_threshold_boundaries():
    # exactly +8.0% from avg triggers scale-out (>= 8.0)
    _, alert = rm.evaluate_position("UUUU", 14.17 * 1.08, 14.17, 13.00, 15.30)
    assert alert is not None and "scale-out" in alert
    # exactly +7.9% does not
    _, alert = rm.evaluate_position("UUUU", 14.17 * 1.079, 14.17, 13.00, 15.30)
    assert alert is None
