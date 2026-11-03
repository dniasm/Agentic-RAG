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

    def test_list_after_a_cited_lead_in_is_covered(self):
        ev = ["The controller shall designate a data protection officer in any case where the processing is carried "
              "out by a public authority, or the core activities involve regular and systematic monitoring of data subjects."]
        answer = ("A controller must designate a data protection officer in any of the following cases [1]:\n\n"
                  "1. The processing is carried out by a public authority.\n"
                  "2. The core activities involve regular and systematic monitoring of data subjects.")
        self.assertTrue(c.check(answer, ev).ok)

    def test_short_sentence_with_a_number_is_still_checked(self):
        ev = ["Nepal has three tiers of government: federal, seven provincial and 753 local governments."]
        self.assertTrue(c.check("Nepal has 753 local governments [1].", ev).ok)
        self.assertFalse(c.check("Nepal has 760 local governments [1].", ev).ok)

    def test_plurals_and_hyphenated_compounds_match(self):
        ev = ["Where notification is not made within 72 hours, it shall be accompanied by reasons for the delay."]
        self.assertTrue(c.check("If the 72-hour notification is late, reasons for the delay must be given [1].", ev).ok)

    def test_names_from_the_question_and_chemical_formulas(self):
        ev = ["The free parameters are usually chosen as k1 in [1.2, 2.0] and b = 0.75."]
        answer = "The b parameter is typically set to 0.75 in BM25 [1]."
        self.assertFalse(c.check(answer, ev).ok)                                  # BM25 isn't in the passage...
        self.assertTrue(c.check(answer, ev, question="What value is b set to in BM25?").ok)  # ...but it's the topic
        ev = ["In the Calvin cycle, atmospheric carbon dioxide is incorporated into ribulose bisphosphate."]
        self.assertTrue(c.check("The Calvin cycle fixes atmospheric CO₂ into ribulose-1,5-bisphosphate [1].", ev).ok)

    def test_ordinals_decades_and_number_lists_are_read_correctly(self):
        self.assertEqual(c._numbers("k1 in [1.2, 2.0] since the 1970s and the 20th century"), ["1.2", "2.0"])
        self.assertEqual(c._numbers("fines up to 20,000,000 EUR"), ["20000000"])
        self.assertEqual(c._numbers("BM25 and COVID-19"), [])


if __name__ == "__main__":
    unittest.main()
