import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Andrew

CATALOG = [
    {'id': 'grok', 'label': 'Grok', 'ready': True, 'detail': 'ok',
     'models': [{'id': 'grok-4.7-build-fast', 'label': 'Grok 4.7 Build Fast'},
                {'id': 'grok-4.7', 'label': 'Grok 4.7'}]},
    {'id': 'claude', 'label': 'Claude', 'ready': True, 'detail': 'ok',
     'models': [{'id': 'haiku', 'label': 'Haiku'}, {'id': 'sonnet', 'label': 'Sonnet'}],
     'custom_models': True},
]


class VoiceModelSwitchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.app = Andrew(self.tmp.name)
        self.app.set('provider', 'grok')
        self.app.set('grok_model', 'grok-4.7-build-fast')
        self.catalog = mock.patch('providers.catalog', return_value=CATALOG)
        self.catalog.start()
        self.no_ai = mock.patch.object(self.app, 'ai', side_effect=AssertionError('AI should not be called'))
        self.no_ai.start()

    def tearDown(self):
        self.no_ai.stop()
        self.catalog.stop()
        self.app.db.close()
        self.tmp.cleanup()

    def test_switch_model_to_named_model(self):
        answer = self.app.command('switch model to grok 4.7')
        self.assertIn('grok-4.7', answer)
        self.assertEqual(self.app.get('grok_model'), 'grok-4.7')
        self.assertEqual(self.app.get('provider'), 'grok')

    def test_use_the_fast_model(self):
        self.app.set('grok_model', 'grok-4.7')
        self.app.command('use the fast model')
        self.assertEqual(self.app.get('grok_model'), 'grok-4.7-build-fast')

    def test_change_ai_model_for_current_claude_provider(self):
        self.app.set('provider', 'claude')
        self.app.command('change the AI model to sonnet')
        self.assertEqual(self.app.get('claude_model'), 'sonnet')
        self.assertEqual(self.app.get('provider'), 'claude')

    def test_unknown_model_keeps_current(self):
        answer = self.app.command('switch model to banana')
        self.assertIn('Keeping your current model', answer)
        self.assertEqual(self.app.get('grok_model'), 'grok-4.7-build-fast')

    def test_list_models(self):
        answer = self.app.command('list models')
        self.assertIn('Grok 4.7', answer)
        self.assertIn('Grok 4.7 Build Fast', answer)


if __name__ == '__main__':
    unittest.main()
