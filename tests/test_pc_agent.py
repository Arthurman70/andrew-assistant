import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from core import Andrew, ROOT
from pc_agent import PCAgent, decision, is_pc_task, local_allowed
from pc_control import safe_url, click_allowed


class FakePC:
    def __init__(self): self.calls=[]
    def perform(self, action, args):
        self.calls.append((action,args))
        return {'window':42,'title':'Calculator'}


class PCAgentTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'data/tests').mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests')
        self.app=Andrew(self.temp.name)
    def tearDown(self):
        self.app.db.close();self.temp.cleanup()
    def scripted(self, decisions):
        pending=iter(decisions);pc=FakePC()
        agent=PCAgent(self.app,planner=lambda *a:json.dumps(next(pending)),controller_factory=lambda:pc)
        return agent,pc
    def test_compound_request_acts_observes_then_sets_one_timer(self):
        agent,pc=self.scripted([
            {'action':'open','args':{'app':'calculator'}},
            {'action':'inspect','args':{'window':42}},
            {'action':'local','args':{'command':'set a tea timer for five minutes'}},
            {'action':'finish','status':'complete','answer':'Calculator is open and the tea timer is set.'}])
        agent.run('Open Calculator and set a tea timer for five minutes','pi')
        self.assertEqual(agent.status()['state'],'complete')
        self.assertEqual([c[0] for c in pc.calls],['open','inspect'])
        timers=self.app.status()['timers']
        self.assertEqual(len(timers),1);self.assertEqual(timers[0]['source'],'pi')
    def test_cancellation_during_model_call_prevents_next_action(self):
        pc=FakePC()
        def planner(*a):
            agent.cancel()
            return json.dumps({'action':'open','args':{'app':'chrome'}})
        agent=PCAgent(self.app,planner=planner,controller_factory=lambda:pc)
        agent.run('Open Chrome','pc')
        self.assertEqual(agent.status()['state'],'cancelled');self.assertEqual(pc.calls,[])
    def test_question_continuation_is_scoped_to_source_and_expires(self):
        agent,_=self.scripted([{'action':'finish','status':'needs_input','answer':'Which Chromecast?'}])
        agent.run('Cast this tab','pi')
        self.assertTrue(agent.waiting('pi'));self.assertFalse(agent.waiting('pc'))
        agent.pending['pi']=(0,[])
        self.assertFalse(agent.waiting('pi'))
    def test_unsupported_action_cannot_be_executed(self):
        with self.assertRaises(ValueError): decision('{"action":"shell","args":{"command":"cmd.exe"}}')
        self.assertFalse(local_allowed('take a photo'))
        self.assertFalse(local_allowed('open powershell'))
        self.assertFalse(local_allowed('set a timer for five minutes and open a browser'))
        self.assertTrue(local_allowed('set a tea timer for five minutes'))
    def test_cast_claim_requires_observed_confirmation(self):
        agent,_=self.scripted([
            {'action':'windows','args':{}},
            {'action':'finish','status':'complete','answer':'Casting started.'}])
        agent.run('Cast this tab to Living Room','pc')
        self.assertEqual(agent.status()['state'],'failed')
        self.assertIn('could not verify',agent.status()['answer'])
    def test_routing_does_not_send_cast_to_home_assistant(self):
        agent,_=self.scripted([]);self.app.pc_agent=agent
        with patch.object(agent,'start',return_value='Working') as start,patch.object(self.app,'home',side_effect=AssertionError('Not Home Assistant')):
            self.assertEqual(self.app.command('cast Bobobo from my Chrome to my Chromecast'),'Working')
            start.assert_called_once()
    def test_pi_display_and_simple_local_intents_stay_local(self):
        self.assertFalse(is_pc_task('open browser on the Pi','pc'))
        self.assertFalse(is_pc_task('set a five minute timer','pc'))
        self.assertFalse(is_pc_task('switch to OpenAI model GPT six Luna','pc'))
        self.assertTrue(is_pc_task('open Calculator and set a five minute timer','pi'))
        self.assertTrue(is_pc_task('cast the current tab','pi'))
    def test_dangerous_urls_and_controls_are_rejected(self):
        for url in ('javascript:alert(1)','file:///C:/Windows','https://user:pass@example.com','http://127.0.0.1:8765/'):
            with self.assertRaises(ValueError): safe_url(url)
        self.assertEqual(safe_url('https://example.com'),'https://example.com')
        self.assertFalse(click_allowed('Button','Buy now'))
        self.assertFalse(click_allowed('Button','Allow microphone'))
        self.assertFalse(click_allowed('Button','Send message'))
        self.assertTrue(click_allowed('MenuItem','Save and share'))
        self.assertTrue(click_allowed('Button','Living Room TV'))


if __name__=='__main__': unittest.main()
