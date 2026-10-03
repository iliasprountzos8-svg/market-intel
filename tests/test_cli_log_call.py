import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import cli  # noqa: E402


class LogCallRequiresConfidence(unittest.TestCase):
    """A call without a stated confidence can never be calibration-scored (see
    analysis/calibration.py), so --confidence must be required, not optional."""

    def test_missing_confidence_is_rejected(self):
        argv = ["cli.py", "log-call", "--ticker", "NVDA", "--call", "bullish"]
        with patch.object(sys, "argv", argv), \
             patch.object(cli, "get_client", return_value=None), \
             patch.object(cli, "cmd_log_call") as mock_fn, \
             self.assertRaises(SystemExit):
            cli.main()
        mock_fn.assert_not_called()

    def test_confidence_is_parsed_through(self):
        argv = ["cli.py", "log-call", "--ticker", "NVDA", "--call", "bullish", "--confidence", "70"]
        with patch.object(sys, "argv", argv), \
             patch.object(cli, "get_client", return_value="fake-client"), \
             patch.object(cli, "cmd_log_call") as mock_fn:
            cli.main()
        mock_fn.assert_called_once()
        client, args = mock_fn.call_args[0]
        self.assertEqual(client, "fake-client")
        self.assertEqual(args.confidence, 70)


if __name__ == "__main__":
    unittest.main()
