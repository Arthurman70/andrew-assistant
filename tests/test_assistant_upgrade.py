import json
import tempfile
import time
import unittest
from unittest.mock import patch,Mock
from pathlib import Path
import numpy as np
from core import Andrew,ROOT
from communications import phone_number,Communications
from assistant_actions import system_prompt
from speech_engine import spoken_text

class FakeVoice:
    def __init__(self):self.calls=[]
    def status(self):return {'state':'ready','message':'test'}
    def perform(self,*args):self.calls.append(args);return 'Sent.'

class UpgradeTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'data/tests').mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests');self.app=Andrew(self.temp.name)
    def tearDown(self):self.app.db.close();self.temp.cleanup()
    def action(self,name,args):return self.app.actions.consume(json.dumps({'reply':'Done!','commands':[{'name':name,'args':args}]}),'pc')
    def test_memory_persists_but_does_not_cross_people(self):
        self.app.command('my name is Sam');self.app.command('remember my favorite drink is tea')
        self.app.command('my name is Kim');self.assertNotIn('tea',self.app.memory.context('pc'))
        self.app.command('my name is Sam');self.assertIn('tea',self.app.memory.context('pc'))
        other=Andrew(self.temp.name)
        try:other.command('my name is Sam');self.assertIn('tea',other.memory.context('pc'))
        finally:other.db.close()
    def test_automatic_preferences_require_identification(self):
        with patch.object(self.app,'ai',return_value='Okay'):
            self.app.command('I like jazz');self.assertEqual(self.app.memory.snapshot()['memories'],[])
            self.app.command('my name is Sam');self.app.command('I like jazz')
        self.assertIn('jazz',self.app.memory.context('pc'))
        self.app.command('forget everything about me');self.assertEqual(self.app.memory.snapshot()['people'],[])
    def test_sensitive_details_are_not_saved(self):
        self.app.command('my name is Sam');answer=self.app.command('remember my password is abc')
        self.assertIn('do not store',answer);self.assertEqual(self.app.memory.snapshot()['memories'],[])
    def test_unknown_speaker_does_not_inherit_device_identity(self):
        self.app.command('my name is Sam')
        with patch.object(self.app.memory,'embedding',return_value=np.array([0.,1.],dtype=np.float32)):
            self.app.memory.begin_voice(bytes(64000),'pc')
            self.assertIsNone(self.app.memory.current('pc'))
            self.assertNotIn('Sam',self.app.memory.context('pc'))
        self.app.memory.end_voice()
    def test_ambiguous_voice_match_asks_for_identity(self):
        for name,vector in [('Sam',[1.,0.]),('Kim',[.999,.04])]:
            self.app.command('my name is '+name);person=self.app.memory.current('pc')
            v=np.asarray(vector,dtype=np.float32);v/=np.linalg.norm(v);self.app.memory.enroll(person,'pc',v)
        with patch.object(self.app.memory,'embedding',return_value=np.array([1.,0.],dtype=np.float32)):
            self.app.memory.begin_voice(bytes(64000),'pc');self.assertIsNone(self.app.memory.current('pc'))
        self.app.memory.end_voice()
    def test_explicit_voice_enrollment_stays_local(self):
        self.app.request.voice_context=True;self.app.request.embedding=np.array([1.,0.],dtype=np.float32)
        self.app.command('my name is Sam');self.app.memory.end_voice()
        with patch.object(self.app.memory,'embedding',return_value=np.array([1.,0.],dtype=np.float32)):
            self.app.memory.begin_voice(bytes(64000),'pc');self.assertEqual(self.app.memory.name(self.app.memory.current('pc')),'Sam')
        self.app.memory.end_voice();self.assertFalse(any(Path(self.temp.name).glob('*.wav')))
    def test_lists_deduplicate_and_survive_restart(self):
        self.app.command('add milk to my shopping list');self.app.command('add Milk to my shopping list')
        self.assertEqual(self.app.daily.lists()['shopping'],['milk'])
        second=Andrew(self.temp.name)
        try:self.assertIn('milk',second.command('read my shopping list'))
        finally:second.db.close()
        self.app.command('remove milk from my shopping list');self.assertEqual(self.app.daily.lists()['shopping'],[])
    def test_reminder_due_on_originating_device(self):
        self.app.command('remind me to stretch in ten minutes','pi')
        row=self.app.status()['timers'][0];self.assertEqual(row['kind'],'reminder');self.assertEqual(row['source'],'pi')
        self.assertEqual(self.app.due(time.time()+610)[0]['text'],'Reminder: stretch')
    def test_routine_runs_local_commands_and_rejects_arbitrary_code(self):
        self.app.command('create routine focus: set a five minute timer; set your volume to 40 percent')
        self.app.command('run focus');self.assertEqual(self.app.get('pc_volume'),40);self.assertEqual(len(self.app.status()['timers']),1)
        with self.assertRaises(ValueError):self.app.command('create routine danger: run powershell')
    def test_calculator_rejects_code(self):
        self.assertIn('20',self.app.daily.calculate('(2 + 3) * 4'))
        with self.assertRaises(ValueError):self.app.daily.calculate('__import__("os")')
    def test_chess_rejects_illegal_and_supports_spoken_squares(self):
        self.app.command('play chess');self.assertIn('not a legal',self.app.command('move e2 to e5'))
        self.assertEqual(self.app.games.state('pc')['moves'],[])
        with patch.object(self.app.games,'choose_chess',side_effect=lambda board:next(iter(board.legal_moves))):
            answer=self.app.command('move e two to e four')
        self.assertIn('Your move',answer);self.assertEqual(len(self.app.games.state('pc')['moves']),2)
        self.app.command('undo move');self.assertEqual(self.app.games.state('pc')['moves'],[])
    def test_games_are_device_specific(self):
        self.app.command('play chess','pi');self.app.command('play trivia','pc')
        self.assertEqual(self.app.games.state('pi')['kind'],'chess');self.assertEqual(self.app.games.state('pc')['kind'],'trivia')
    def test_tic_tac_toe_never_overwrites_occupied_square(self):
        self.app.command('play tic tac toe');self.app.command('square five')
        cells=self.app.games.state('pc')['cells'];self.assertEqual(cells[4],'X');self.assertIn('taken',self.app.command('square five'))
        self.assertEqual(cells,self.app.games.state('pc')['cells'])
    def test_game_state_does_not_reveal_trivia_answers_or_secret(self):
        self.app.command('play trivia');self.assertNotIn('questions',self.app.games.snapshot('pc'))
        self.app.command('play guess the number');self.assertNotIn('secret',self.app.games.snapshot('pc'))
    def test_action_plan_is_validated_before_any_mutation(self):
        result=self.app.actions.consume(json.dumps({'reply':'done','commands':[{'name':'timer.set','args':{'name':'tea','seconds':60}},{'name':'shell','args':{'cmd':'calc'}}]}),'pc')
        self.assertIn('unsupported',result);self.assertEqual(self.app.status()['timers'],[])
    def test_action_results_replace_model_success_claim(self):
        self.assertIn('50 percent',self.action('volume.set',{'percent':50,'device':'pi'}))
        self.assertEqual(self.app.get('pi_volume'),50)
    def test_actions_preserve_json_only_chat_reply(self):
        self.assertEqual(self.app.actions.consume('{"reply":"Hello Sam!","commands":[]}','pc'),'Hello Sam!')
        self.assertEqual(self.app.actions.consume('Plain answer','pc'),'Plain answer')
    def test_incomplete_action_json_never_runs_or_reads_raw_json(self):
        answer=self.app.actions.consume('{"reply":"done","commands":[{"name":"timer.set"','pc')
        self.assertIn('no actions were run',answer);self.assertEqual(self.app.status()['timers'],[])
    def test_voice_and_model_switches_bypass_desktop_agent(self):
        self.app.pc_agent=Mock();self.app.pc_agent.waiting.return_value=False
        self.app.command('switch to natural voice');self.assertEqual(self.app.get('voice'),'af_heart')
        with patch('providers.choose',return_value='Selected') as choose:
            self.assertEqual(self.app.command('switch to Claude Sonnet'),'Selected')
            choose.assert_called_once_with(self.app,'claude','sonnet')
        self.app.pc_agent.start.assert_not_called()
    def test_conversational_multiple_actions_are_not_partially_consumed(self):
        with patch.object(self.app,'ai',return_value='Handled both') as ai:
            answer=self.app.command('set a tea timer for five minutes and add milk to my shopping list')
            self.assertEqual(answer,'Handled both');ai.assert_called_once()
            self.assertEqual(self.app.status()['timers'],[])
    def test_every_provider_has_persona_and_command_catalog(self):
        for provider in ('grok','claude','openai','local'):
            system=system_prompt(self.app,provider,'pi');self.assertIn('You are Andrew',system)
            self.assertIn('timer.set',system);self.assertIn('voice.draft_text',system);self.assertIn('separate explicit confirmation',system)
    def test_google_voice_requires_preview_and_separate_confirmation(self):
        driver=FakeVoice();self.app.communications=Communications(self.app,driver)
        self.app.command('save contact Sam as 202-555-0100')
        self.app.command('text Sam saying hello');self.assertEqual(driver.calls,[])
        self.assertIn('no current',self.app.command('confirm text','pi'));self.assertEqual(driver.calls,[])
        self.app.command('confirm text');self.assertEqual(driver.calls,[('text','+12025550100','hello')])
        self.app.command('confirm text');self.assertEqual(len(driver.calls),1)
    def test_expired_or_changed_speaker_cannot_send_draft(self):
        driver=FakeVoice();c=Communications(self.app,driver);self.app.communications=c
        self.app.command('my name is Sam');c.draft('text','2025550100','hello','pc')
        self.app.command('my name is Kim');self.assertIn('speaker changed',c.confirm('pc','text'));self.assertEqual(driver.calls,[])
        c.pending['pc']['expires']=0;self.assertIn('no current',c.confirm('pc','text'))
    def test_models_can_only_draft_messages_not_confirm_them(self):
        driver=FakeVoice();self.app.communications=Communications(self.app,driver)
        self.action('voice.draft_text',{'recipient':'2025550100','message':'test'});self.assertEqual(driver.calls,[])
        self.assertIn('unsupported',self.action('voice.confirm',{}));self.assertEqual(driver.calls,[])
    def test_short_unmatched_confirmation_prompts_for_a_longer_phrase(self):
        driver=FakeVoice();self.app.communications=Communications(self.app,driver)
        self.app.command('my name is Sam');person=self.app.memory.current('pc')
        self.app.communications.draft('text','2025550100','test','pc')
        self.app.request.voice_context=True;self.app.request.person=None
        self.assertIn('short request',self.app.command('confirm text'));self.assertEqual(driver.calls,[])
        self.app.request.person=person
        self.app.command('confirm sending this text message now please')
        self.assertEqual(driver.calls,[('text','+12025550100','test')]);self.app.memory.end_voice()
    def test_phone_validation_rejects_emergency_and_command_strings(self):
        self.assertEqual(phone_number('(202) 555-0100'),'+12025550100')
        for value in ('911','+1;run cmd','123','https://example.com'):
            with self.assertRaises(ValueError):phone_number(value)
    def test_speech_does_not_read_markdown_or_code(self):
        self.assertEqual(spoken_text('**Hello** [Sam](https://example.com).'),'Hello Sam.')
        self.assertNotIn('print',spoken_text('```python\nprint(1)\n```'))
