import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from keyword_grammar import KeywordGrammar, confusable_names
from wake_detector import WakeDetector


def words(name='tom', confidence=1.):
    return [{'word':'hey','start':.1,'end':.3,'conf':confidence},
            {'word':name,'start':.3,'end':.65,'conf':confidence}]


class GrammarTests(unittest.TestCase):
    def setUp(self):
        self.native=Mock()
        self.native.AcceptWaveform.return_value=False
        self.factory=Mock(return_value=self.native)
        self.patch=patch.dict('sys.modules',vosk=SimpleNamespace(KaldiRecognizer=self.factory))
        self.patch.start();self.addCleanup(self.patch.stop)
        self.detector=KeywordGrammar(Mock(),'tom',['hey tom'],lambda word:True)

    def feed(self, entries, final=False, text=''):
        self.native.AcceptWaveform.return_value=final
        result={'result' if final else 'partial_result':entries,'text':text,'partial':text}
        self.native.Result.return_value=json.dumps(result)
        self.native.PartialResult.return_value=json.dumps(result)
        return self.detector.accept(bytes(32000))

    def test_exact_address_returns_only_verified_sample_boundaries(self):
        self.assertEqual(self.feed(words()),(1600,10400))
        self.assertEqual(self.detector.samples,16000)
        self.assertFalse(hasattr(self.detector,'transcript'))

    def test_complete_result_and_partial_use_same_gate(self):
        self.assertEqual(self.feed(words(),final=True),(1600,10400))

    def test_bare_name_different_name_and_unknown_speech_never_wake(self):
        for entries in (words()[1:],words('tim'),[{'word':'[unk]'}],[]):
            with self.subTest(entries=entries):
                self.detector.reset();self.assertIsNone(self.feed(entries))

    def test_plain_text_without_word_evidence_cannot_wake(self):
        self.assertIsNone(self.feed([],text='hey tom'))

    def test_low_confidence_and_invalid_timestamps_do_not_wake(self):
        for entries in (words(confidence=.8),words(confidence=float('nan')),
                        [dict(words()[0],start=-1),words()[1]],
                        [words()[0],dict(words()[1],end=2)],
                        [words()[0],dict(words()[1],start=.9,end=1.)]):
            with self.subTest(entries=entries):
                self.detector.reset();self.assertIsNone(self.feed(entries))

    def test_stale_or_repeated_keyword_never_reuses_earlier_room_audio(self):
        self.detector.samples=160000
        self.assertIsNone(self.feed(words()))
        self.detector.reset();self.assertIsNotNone(self.feed(words()))
        self.assertIsNone(self.feed(words()))

    def test_reset_constructs_new_recognizer_and_zeroes_timestamps(self):
        self.feed(words());self.detector.reset()
        self.assertEqual(self.factory.call_count,1)
        self.assertEqual(self.detector.samples,0)
        self.native.Reset.assert_not_called()
        self.assertEqual(self.feed(words()),(1600,10400))
        self.assertEqual(self.factory.call_count,2)

    def test_blocked_playback_frames_do_not_rebuild_decoder_repeatedly(self):
        for _ in range(500):self.detector.reset()
        self.factory.assert_not_called()
        self.feed(words());self.factory.assert_called_once()

    def test_grammar_always_has_unknown_rejection_and_exact_phrase(self):
        self.feed([])
        grammar=json.loads(self.factory.call_args.args[2])
        self.assertIn('hey tom',grammar);self.assertIn('hey tim',grammar)
        self.assertIn('[unk]',grammar);self.assertNotIn('tom',grammar)
        self.assertEqual(self.detector.targets,[['hey','tom']])

    def test_name_that_equals_prefix_keeps_prefix_in_rejection_paths(self):
        with patch('keyword_grammar.confusable_names',return_value=['hay']):
            detector=KeywordGrammar(Mock(),'hey',['hey hey'],lambda word:True)
        self.assertIn('hey hay',json.loads(detector.grammar))
        self.assertNotIn('hay hey',json.loads(detector.grammar))

    def test_unknown_name_never_degrades_to_hey_only(self):
        model=Mock();model.vosk_model_find_word.side_effect=lambda word:-1 if word=='unknownname' else 1
        with patch('keyword_grammar.model_path',return_value=Path('/model')),patch('keyword_grammar.load_model',return_value=model):
            self.assertIsNone(KeywordGrammar.available('/wake','Unknownname',['hey unknownname']))
        self.assertEqual(self.factory.call_count,0)

    def test_missing_or_broken_model_falls_back_without_general_recognition(self):
        with patch('keyword_grammar.model_path',return_value=None):
            self.assertIsNone(KeywordGrammar.available('/wake','Tom',['hey tom']))
        with patch('keyword_grammar.model_path',return_value=Path('/model')),patch('keyword_grammar.load_model',side_effect=RuntimeError()):
            self.assertIsNone(KeywordGrammar.available('/wake','Tom',['hey tom']))
        self.assertEqual(self.factory.call_count,0)

    def test_confusable_names_are_bounded_and_known(self):
        self.assertEqual(confusable_names('tom',lambda word:word=='tim'),['tim'])
        self.assertEqual(confusable_names('tom',lambda word:True),['tim'])
        self.assertLessEqual(len(confusable_names('aeiou aeio u',lambda word:True)),24)

    def test_detector_keeps_grammar_clock_and_post_address_boundary(self):
        detector=WakeDetector.__new__(WakeDetector);detector.grammar=self.detector
        detector.reset()
        self.native.PartialResult.return_value=json.dumps({'partial_result':words()})
        self.assertEqual(detector.accept(bytes(32000)),1600)
        self.assertEqual(detector.samples,16000)
        self.assertEqual(detector.command_start,10400)


if __name__=='__main__':unittest.main()
