import tempfile
import time
import unittest
from unittest.mock import patch
from core import Andrew, ROOT


class CoreTests(unittest.TestCase):
    def test_improvement_schema_is_forwarded_to_grok_without_action_execution(self):
        schema={'type':'object','properties':{'paths':{'type':'array'}}}
        with patch('grok_provider.chat',return_value='{"paths":["core.py"]}') as chat, \
             patch.object(self.app.actions,'consume',side_effect=AssertionError('Do not execute coding output')):
            self.assertEqual(self.app.ai('Choose files','grok','grok-selected',purpose='improvement',response_schema=schema),'{"paths":["core.py"]}')
        self.assertEqual(chat.call_args.kwargs['response_schema'],schema)
        self.assertEqual(chat.call_args.kwargs['timeout'],240)

    def setUp(self):
        parent = ROOT / 'data' / 'tests'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.app = Andrew(self.temp.name)

    def tearDown(self):
        self.app.db.close()
        from pathlib import Path
        assert Path(self.temp.name).resolve().is_relative_to((ROOT / 'data' / 'tests').resolve())
        self.temp.cleanup()

    def test_local_improvement_uses_json_and_larger_budget_without_conversation_history(self):
        with patch('core.request_json',side_effect=[{'models':[{'name':'qwen3:0.6b'}]},
                 {'message':{'content':'{"paths":["core.py"]}'}}]) as request:
            result=self.app.ai('Choose source files.','local','qwen3:0.6b',purpose='improvement')
        self.assertEqual(result,'{"paths":["core.py"]}')
        payload=request.call_args.args[1]
        self.assertEqual(payload['format'],'json')
        self.assertEqual(payload['options']['temperature'],0)
        self.assertGreater(payload['options']['num_predict'],250)
        self.assertEqual(len(payload['messages']),2)
        self.assertFalse(self.app.conversations)

    def test_name_survives_restart_and_new_wake_prefix(self):
        self.app.command('Andrew, change your name to Charlie')
        second = Andrew(self.temp.name)
        self.assertEqual(second.get('name'), 'Charlie')
        self.assertEqual(second.command('Charlie what is your name'), 'My name is Charlie.')
        second.db.close()

    def test_timer_local_durable_edit_cancel(self):
        with patch.object(self.app, 'ai', side_effect=AssertionError('No AI for timers')):
            self.app.command('set a tea timer for five minutes')
            self.assertEqual(len(self.app.status()['timers']), 1)
            self.app.command('change tea timer to two minutes')
            row = self.app.status()['timers'][0]
            self.assertAlmostEqual(row['due']-time.time(), 120, delta=2)
            self.assertEqual(len(self.app.due(time.time()+121)), 1)
            self.assertEqual(self.app.due(time.time()+200), [])
            self.app.command('cancel tea timer')
            self.assertEqual(self.app.status()['timers'], [])

    def test_natural_rename_phrases_are_local_and_update_wake_address(self):
        phrases=('I\x27ll call you Charlie','I want to call you Charlie','your new name is Charlie',
                 'from now on, call yourself Charlie','you are now called Charlie','set your name to Charlie')
        for phrase in phrases:
            self.app.set('name','Andrew')
            with self.subTest(phrase=phrase),patch.object(self.app,'ai',side_effect=AssertionError('Rename reached AI')):
                answer=self.app.command('Hey Andrew, '+phrase)
                self.assertIn('Hey Charlie',answer)
                self.assertEqual(self.app.get('name'),'Charlie')
                self.assertEqual(self.app.command('Hey Charlie, what is your name'),'My name is Charlie.')

    def test_assistant_rename_is_independent_of_speaker_and_active_game(self):
        self.app.command('my name is Sam')
        self.app.command('play chess')
        self.app.command('call yourself Alex')
        self.assertEqual(self.app.get('name'),'Alex')
        self.assertEqual(self.app.memory.current('pc'),self.app.memory.snapshot()['active']['pc']['id'])
        self.assertEqual(self.app.memory.name(self.app.memory.current('pc')),'Sam')

    def test_rename_guidance_and_invalid_names_do_not_call_ai_or_change_name(self):
        with patch.object(self.app,'ai',side_effect=AssertionError('Rename reached AI')):
            self.assertIn('Hey Andrew, call yourself Charlie',self.app.command('change your name'))
            for name in ('123','Alex/../../','Charlie and then open browser','x'*33):
                with self.subTest(name=name),self.assertRaises(ValueError):self.app.command('call yourself '+name)
                self.assertEqual(self.app.get('name'),'Andrew')

    def test_camera_never_opens_for_chat_or_offline(self):
        self.assertIn('offline', self.app.command('take a photo'))
        self.assertIsNone(self.app.job()['job'])
        with patch.object(self.app, 'ai', return_value='Camera not accessed.'):
            self.app.command('What do you see')
        self.assertIsNone(self.app.job()['job'])
        self.assertIn('not enabled', self.app.command('watch camera 24/7'))

    def test_camera_one_shot_cannot_replay(self):
        self.app.relay_seen = time.time()
        self.app.command('take a photo')
        job = self.app.job()['job']
        self.assertIsNone(self.app.job()['job'])
        self.app.accept_photo(job['id'], b'\xff\xd8test')
        with self.assertRaises(ValueError):
            self.app.accept_photo(job['id'], b'\xff\xd8test')

    def test_cancel_and_expiry_revoke_photo_upload(self):
        self.app.relay_seen = time.time()
        self.app.command('take a photo')
        job = self.app.job()['job']
        self.app.command('stop camera')
        with self.assertRaises(ValueError):
            self.app.accept_photo(job['id'], b'\xff\xd8test')
        self.app.command('take a photo')
        job = self.app.job()['job']
        with self.app.db:
            self.app.db.execute('UPDATE jobs SET expires=0')
        with self.assertRaises(ValueError):
            self.app.accept_photo(job['id'], b'\xff\xd8test')

    def test_grok_default_and_explicit_provider_request(self):
        with patch.object(self.app, 'ai', return_value='answer') as ai:
            self.app.command('Tell me a joke')
            ai.assert_called_once_with('Tell me a joke')
            self.assertEqual(self.app.get('provider'), 'grok')
            ai.reset_mock()
            self.app.command('ask openai explain gravity')
            ai.assert_called_once_with('explain gravity', 'openai', None)
        with patch('providers.catalog',return_value=[{'id':'local','ready':False,'detail':'Offline'}]):
            self.assertIn('Keeping your current provider',self.app.command('use local'))
        self.assertEqual(self.app.get('provider'), 'grok')

    def test_source_volume_is_independent_and_local(self):
        with patch.object(self.app,'ai',side_effect=AssertionError('No AI for volume')):
            self.app.command('set your volume to 30 percent',source='pi')
            self.assertEqual(self.app.get('pi_volume'),30)
            self.assertEqual(self.app.get('pc_volume'),80)
            self.app.command('turn yourself down on the PC',source='pi')
            self.assertEqual(self.app.get('pc_volume'),70)
            self.app.command('mute yourself',source='pi')
            self.assertEqual(self.app.get('pi_volume'),0)
            self.app.command('unmute yourself',source='pi')
            self.assertEqual(self.app.get('pi_volume'),30)

    def test_pi_browser_job_expiry_and_completion(self):
        self.app.relay_seen=time.time()
        self.app.command('open https://example.com on the Pi')
        job=self.app.job()['job']
        self.assertEqual(job['kind'],'display')
        self.app.complete_job(job['id'])
        with self.assertRaises(ValueError): self.app.complete_job(job['id'])
        self.app.command('close browser',source='pi')
        job=self.app.job()['job']
        with self.app.db: self.app.db.execute('UPDATE jobs SET expires=0')
        with self.assertRaises(ValueError): self.app.complete_job(job['id'])

    def test_home_requires_setup_and_does_not_fall_through_to_ai(self):
        with patch.object(self.app, 'ai', side_effect=AssertionError('No AI')):
            self.assertIn('No device action', self.app.command('turn on kitchen lights'))

    def test_invalid_durations_and_alarm(self):
        for duration in ('zero seconds', '-1 seconds', '999 hours'):
            with self.assertRaises(ValueError):
                self.app.seconds(duration)
        with self.assertRaises(ValueError):
            self.app.command('set alarm for 25:70')
        self.assertIn('Alarm set', self.app.command('set alarm for 7:30 am'))

    def test_timer_remembers_requesting_speaker(self):
        self.app.command('set a 5-minute timer called tea',source='pi')
        self.assertEqual(self.app.status()['timers'][0]['source'],'pi')
        notifications = self.app.due(time.time()+301)
        self.assertEqual(notifications,[{'text':'Your tea timer is finished.','source':'pi'}])

    def test_voice_model_names_select_and_persist(self):
        from providers import choose
        catalog = [{'id':'openai','label':'OpenAI','ready':True,'models':[{'id':'gpt-6-luna','label':'GPT-6 Luna'}]}]
        with patch('providers.catalog',return_value=catalog):
            self.app.command('switch to OpenAI model GPT six Luna')
            self.assertEqual(self.app.get('provider'),'openai')
            self.assertEqual(self.app.get('openai_model'),'gpt-6-luna')
            second = Andrew(self.temp.name)
            self.assertEqual(second.get('openai_model'),'gpt-6-luna')
            second.db.close()


if __name__ == '__main__':
    unittest.main()
