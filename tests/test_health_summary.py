import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "homelab" / "agents"))
import health_summary as hs  # noqa: E402

GOOD = """## uptime
up 8 hours, 30 minutes
## disk
/dev/nvme0n1p2  226G   13G  202G   6% /
## restarting/unhealthy
## failed services
## service failures last 24h
market-intel: 0
homelab-backup: 0
## last backup result
Result=success
## ssd health
SMART overall-health self-assessment test result: PASSED
critical_warning                    : 0
percentage_used                     : 30%
## pending updates
0
## usb backup (second copy)
last ok: 2026-09-26 18:29 (5 h ago)
## reboot pending
no
## market intel last cycle
finished 2026-09-27T00:40:01+03:00 rc=0
"""


class Summary(unittest.TestCase):
    def test_all_good(self):
        out = hs.summarize(GOOD)
        self.assertTrue(out.startswith("STATUS: OK"))
        self.assertIn("SSD 30% worn", out)
        self.assertIn("disk 6% used", out)

    def test_warnings(self):
        bad = (GOOD.replace("6% /", "91% /").replace("market-intel: 0", "market-intel: 3")
               .replace("## reboot pending\nno", "## reboot pending\nyes").replace("(5 h ago)", "(100 h ago)")
               .replace("percentage_used                     : 30%", "percentage_used                     : 75%"))
        out = hs.summarize(bad)
        self.assertTrue(out.startswith("STATUS: WARN"))
        for want in ("disk 91%", "market-intel x3", "reboot is pending", "USB backup last succeeded 4 days", "SSD 75% worn"):
            self.assertIn(want, out)

    def test_failed_backup_is_fail(self):
        self.assertTrue(hs.summarize(GOOD.replace("Result=success", "Result=exit-code")).startswith("STATUS: FAIL"))

    def test_ssd_critical_warning_is_fail(self):
        self.assertTrue(hs.summarize(GOOD.replace("critical_warning                    : 0", "critical_warning                    : 0x04")).startswith("STATUS: FAIL"))

    def test_cycle_failure_warns(self):
        self.assertIn("rc=1", hs.summarize(GOOD.replace("rc=0", "rc=1")))

    def test_empty_input_does_not_crash(self):
        self.assertTrue(hs.summarize("").startswith("STATUS: OK"))

    def test_at_most_six_bullets(self):
        text = GOOD.replace("## restarting/unhealthy", "## restarting/unhealthy\nx y z").replace("0\n## usb", "99\n## usb")
        self.assertLessEqual(sum(1 for l in hs.summarize(text).splitlines() if l.startswith("- ")), 7)


if __name__ == "__main__":
    unittest.main()
