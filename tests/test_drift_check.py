import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "homelab"))
import drift_check as dc  # noqa: E402


class Mapping(unittest.TestCase):
    def test_deployed_dirs_and_scripts_map(self):
        self.assertEqual(dc.server_path("analysis/scoring.py"), "analysis/scoring.py")
        self.assertEqual(dc.server_path("homelab/scripts/run_lab_weekly.sh"), "run_lab_weekly.sh")
        self.assertEqual(dc.server_path("fdr.py"), "fdr.py")

    def test_not_deployed_paths_ignored(self):
        for p in ("dashboard/app/page.tsx", "tests/test_scoring.py", "analysis/.venv/Lib/x.py", "analysis/requirements.txt",
                  "analysis/eval/labels_v1.txt", "homelab/hq/hq_app.py"):
            self.assertIsNone(dc.server_path(p), p)


class Compare(unittest.TestCase):
    def test_line_endings_ignored(self):
        self.assertEqual(dc.norm_hash(b"a\r\nb\r\n"), dc.norm_hash(b"a\nb\n"))

    def test_differs_and_missing(self):
        local = {"a.py": ("a.py", "1"), "b.py": ("b.py", "2"), "c.py": ("c.py", "3")}
        self.assertEqual(dc.compare(local, {"a.py": "1", "b.py": "X", "c.py": "MISSING"}), (["b.py"], ["c.py"]))


if __name__ == "__main__":
    unittest.main()
