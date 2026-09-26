import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import reader  # noqa: E402


class Prompt(unittest.TestCase):
    def test_prompt_lists_items_and_vocabulary(self):
        p = reader.build_prompt([{"i": 0, "title": "Nvidia beats estimates", "source": "Reuters", "summary": "Revenue rose"},
                                 {"i": 1, "title": "Should you buy Nike?", "source": "Yahoo Finance", "summary": ""}])
        self.assertIn("[0] (Reuters) Nvidia beats estimates", p)
        self.assertIn("[1] (Yahoo Finance) Should you buy Nike?", p)
        self.assertIn("opinion", p)
        self.assertIn("Return ONLY a JSON array", p)

    def test_full_text_is_preferred_and_truncated(self):
        p = reader.build_prompt([{"i": 0, "title": "T", "source": "S", "summary": "short", "text": "x" * 5000}])
        self.assertNotIn("short", p)
        self.assertLess(len(p), len(reader.PROMPT_HEAD) + 1200)


class Parse(unittest.TestCase):
    def test_parses_clean_array(self):
        txt = '[{"i":0,"event":"Earnings","about":"nvda","direction":1,"magnitude":3,"relevant":1,"takeaway":"Beat."}]'
        r = reader.parse_response(txt, {0})
        self.assertEqual(r[0]["event"], "earnings")
        self.assertEqual(r[0]["about"], "NVDA")
        self.assertEqual(r[0]["direction"], 1)

    def test_tolerates_code_fences_and_prose(self):
        txt = 'Here you go:\n```json\n[{"i": 1, "event": "m&a", "about": "", "direction": 0, "magnitude": 2, "relevant": 1, "takeaway": "x"}]\n```'
        self.assertEqual(reader.parse_response(txt, {1})[1]["event"], "m&a")

    def test_unknown_event_and_out_of_range_values_are_clamped(self):
        txt = '[{"i":0,"event":"banana","about":"","direction":5,"magnitude":9,"relevant":3,"takeaway":""}]'
        r = reader.parse_response(txt, {0})[0]
        self.assertEqual((r["event"], r["direction"], r["magnitude"], r["relevant"]), ("other", 1, 3, 1))

    def test_bad_input_returns_nothing_not_guesses(self):
        self.assertEqual(reader.parse_response("no json here", {0}), {})
        self.assertEqual(reader.parse_response("[{broken", {0}), {})
        self.assertEqual(reader.parse_response('[{"i": 9, "event": "other"}]', {0}), {})  # index not requested


if __name__ == "__main__":
    unittest.main()
