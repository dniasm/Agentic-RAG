"""Citation checks in code (no API calls).

    python -m unittest backend.tests.test_citations -v
"""
import unittest

from backend.evaluation import citations as c

EVIDENCE = [
    "The ISP complaint classification system is built with Python and Flask, and MySQL stores the complaints.",
    "Complaints are converted into TF-IDF features and a linear SVM classifier assigns the category; "
    "accuracy reached 92.5% on 1,200 test complaints.",
]
GOOD = ("The system uses Python with Flask, and MySQL stores complaints [1]. A linear SVM classifies complaints "
        "using TF-IDF features, reaching 92.5% accuracy on 1,200 test complaints [2].")


class CitationChecks(unittest.TestCase):
    def test_a_well_cited_answer_passes(self):
        self.assertTrue(c.check(GOOD, EVIDENCE).ok)

    def test_claim_cited_to_the_wrong_passage(self):
        self.assertFalse(c.check(GOOD.replace("[2]", "[1]"), EVIDENCE).ok)

    def test_changed_number(self):
        r = c.check(GOOD.replace("1,200", "1,900"), EVIDENCE)
        self.assertFalse(r.ok)
        self.assertEqual(r.unsupported[0].missing_numbers, ["1900"])

    def test_changed_name_with_a_number(self):
        ev = ["BM25 is a ranking function used by search engines to score documents."]
        self.assertTrue(c.check("BM25 is a ranking function search engines use to score documents [1].", ev).ok)
        self.assertFalse(c.check("BM32 is a ranking function search engines use to score documents [1].", ev).ok)

    def test_invented_claim_with_a_citation(self):
        bad = GOOD + "\n\nThe programme was funded by a grant from the Asian Development Bank in 1997 [1]."
        self.assertFalse(c.check(bad, EVIDENCE).ok)

    def test_uncited_claim_in_the_middle(self):
        bad = ("The system uses Python with Flask [1].\n\nEarly versions were written in COBOL and ran on IBM "
               "mainframes.\n\nA linear SVM classifies complaints using TF-IDF features [2].")
        self.assertEqual(len(c.check(bad, EVIDENCE).uncited), 1)

    def test_citation_to_a_missing_passage(self):
        self.assertEqual(c.check(GOOD.replace("[2]", "[7]"), EVIDENCE).invalid, [7])

    def test_lead_ins_summaries_and_cited_lists_need_no_own_citation(self):
        answer = ("The system was built with a Python stack:\n\n- Python and Flask power the complaint system\n"
                  "- MySQL stores the complaints [1]\n\nTogether, these components form the technology stack.")
        self.assertTrue(c.check(answer, EVIDENCE).ok)

    def test_ordinals_decades_and_number_lists_are_read_correctly(self):
        self.assertEqual(c._numbers("k1 in [1.2, 2.0] since the 1970s and the 20th century"), ["1.2", "2.0"])
        self.assertEqual(c._numbers("fines up to 20,000,000 EUR"), ["20000000"])
        self.assertEqual(c._numbers("BM25 and COVID-19"), [])


if __name__ == "__main__":
    unittest.main()
