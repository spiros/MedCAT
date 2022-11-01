import json
import re
import unittest
import yaml

from medcat.utils.regression.checking import RegressionChecker

from medcat.utils.regression.converting import PerSentenceSelector, PerWordContextSelector, medcat_export_json_to_regression_yml
from medcat.utils.regression.targeting import TargetInfo


class FakeTranslationLayer:

    def __init__(self, mct_export: dict) -> None:
        self.mct_export = json.loads(mct_export)

    def all_targets(self):  # -> Iterator[TargetInfo]:
        for project in self.mct_export['projects']:
            for doc in project['documents']:
                for ann in doc['annotations']:
                    yield TargetInfo(ann['cui'], ann['value'])


class TestConversion(unittest.TestCase):
    def_file_name = 'tests/resources/medcat_trainer_export.json'
    _converted_yaml = None
    _mct_export = None

    @property
    def converted_yaml(self):
        if not self._converted_yaml:
            self._converted_yaml = medcat_export_json_to_regression_yml(
                self.def_file_name)
        return self._converted_yaml

    @property
    def mct_export(self):
        if not self._mct_export:
            with open(self.def_file_name, 'r') as f:
                self._mct_export = f.read()
        return self._mct_export

    def test_conversion_default_gets_str(self):
        self.assertIsInstance(self.converted_yaml, str)
        self.assertGreater(len(self.converted_yaml), 0)

    def test_conversion_default_gets_yml(self):
        d = yaml.safe_load(self.converted_yaml)
        self.assertIsInstance(d, dict)
        self.assertGreater(len(d), 0)

    def test_conversion_valid_regression_case(self):
        d = yaml.safe_load(self.converted_yaml)
        checker = RegressionChecker.from_dict(d)
        self.assertIsInstance(checker, RegressionChecker)

    def test_correct_number_of_cases(self):
        checker = RegressionChecker.from_dict(
            yaml.safe_load(self.converted_yaml))
        expected = self.mct_export.count('"cui":')
        nr_of_cases = len(list(checker.get_all_subcases(
            FakeTranslationLayer(self.mct_export))))
        self.assertEqual(nr_of_cases, expected)

    def test_cases_have_1_replacement_part(self):
        checker = RegressionChecker.from_dict(
            yaml.safe_load(self.converted_yaml))
        for case, ti, phrase in checker.get_all_subcases(FakeTranslationLayer(self.mct_export)):
            with self.subTest(f'With phrase {phrase} and {case} and {ti}'):
                replaced = phrase % 'something'
                self.assertIsInstance(replaced, str)


class TestSelectors(unittest.TestCase):
    words_before = 2
    words_after = 3

    def test_PerWordContext_contains_concept(self, text='some random text with #TEST# stuff and'
                                             ' then some more text', find='#TEST#'):
        found = re.search(find, text)
        start, end = found.start(), found.end()
        pwcs = PerWordContextSelector(self.words_before, self.words_after)
        context = pwcs.get_context(text, start, end, leave_concept=True)
        self.assertIn(find, context)

    def test_PerWordContextSelector_selects_words_both_sides_plenty(self,
                                                                    text='with some text here #TEST# and some text after',
                                                                    find='#TEST#'):
        found = re.search(find, text)
        start, end = found.start(), found.end()
        pwcs = PerWordContextSelector(self.words_before, self.words_after)
        context = pwcs.get_context(text, start, end, leave_concept=True)
        expected_words = self.words_before + \
            self.words_after + 1  # 1 for the word to be found
        nr_of_original_words = len(text.split())
        nr_of_words_in_context = len(context.split())
        self.assertLessEqual(nr_of_words_in_context, nr_of_original_words)
        self.assertEqual(nr_of_words_in_context, expected_words)
        return context

    def test_PerWordContextSelector_selects_words_both_sides_short(self,
                                                                   text='one #TEST# each',
                                                                   find='#TEST#'):
        found = re.search(find, text)
        start, end = found.start(), found.end()
        pwcs = PerWordContextSelector(self.words_before, self.words_after)
        context = pwcs.get_context(text, start, end, leave_concept=True)
        nr_of_original_words = len(text.split())
        expected_words = nr_of_original_words  # all
        nr_of_words_in_context = len(context.split())
        self.assertEqual(nr_of_words_in_context, expected_words)

    def test_PerWordContextSelector_no_care_sentences(self,
                                                      text='sentence ends. #TEST# here. '
                                                      'And more stuff',
                                                      find='#TEST#'):
        context = self.test_PerWordContextSelector_selects_words_both_sides_plenty(
            text, find)
        self.assertIn('.', context)

    def test_PerSentenceSelector_contains_concept(self, text='other sentence ends.'
                                                  ' some random text with #TEST# stuff and'
                                                  ' then sentence ends.'
                                                  ' some more text', find='#TEST#'):
        found = re.search(find, text)
        start, end = found.start(), found.end()
        psc = PerSentenceSelector()
        context = psc.get_context(text, start, end, leave_concept=True)
        self.assertIn(find, context)

    def test_PerSentenceSelector_selects_sentence_ends_long(self, text='Prev sent. Now #TEST# sentence that ends with a lot of words.'
                                                            'And then there is more sentences. And more.', find='#TEST#'):
        found = re.search(find, text)
        start, end = found.start(), found.end()
        psc = PerSentenceSelector()
        context = psc.get_context(text, start, end, leave_concept=True)
        self.assertIsNone(re.search(psc.stoppers, context))
        self.assertLessEqual(len(context), len(text))
        man_found = text[text.rfind(
            '.', 0, start) + 1: text.find('.', end)].strip()
        self.assertEqual(context, man_found)

    def test_PerSentenceSelector_selects_first_sent(self, text='First #TEST# sentence. That ends early.'
                                                    'And then there is more sentences. And more.', find='#TEST#'):
        found = re.search(find, text)
        start, end = found.start(), found.end()
        psc = PerSentenceSelector()
        context = psc.get_context(text, start, end, leave_concept=True)
        self.assertIn(context, text)
        self.assertTrue(text.startswith(context))

    def test_PerSentenceSelector_selects_last_sent(self, text='Firs there are sentences.'
                                                   'And then there are more. Finally, we have #TEST# word',
                                                   find='#TEST#'):
        found = re.search(find, text)
        start, end = found.start(), found.end()
        psc = PerSentenceSelector()
        context = psc.get_context(text, start, end, leave_concept=True)
        self.assertIn(context, text)
        self.assertTrue(text.endswith(context))
