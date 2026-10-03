import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import Andrew, ROOT
from command_controls import chain_parts
from improvements import manager, save_review

CATALOG=[
    {'id':'grok','label':'Grok','ready':True,'models':[{'id':'grok-4.7','label':'Grok 4.7'},{'id':'grok-4.7-build-fast','label':'Grok 4.7 Build Fast'}]},
    {'id':'claude','label':'Claude','ready':True,'models':[{'id':v,'label':v.title()} for v in ('haiku','sonnet','opus')]},
    {'id':'openai','label':'OpenAI','ready':True,'models':[{'id':'','label':'Account default'}]},
    {'id':'local','label':'Local','ready':True,'models':[{'id':'qwen3:0.6b','label':'Qwen'}]},
]


class ControlTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'data/tests').mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests')
        self.app=Andrew(self.temp.name)
        self.ai=patch.object(self.app,'ai',side_effect=AssertionError('App control reached the cloud')).start()
        patch('providers.catalog',return_value=CATALOG).start()
    def tearDown(self):
        patch.stopall();self.app.db.close();self.temp.cleanup()

    def proposal(self, identity='20261003-081410-aaaaaa', status='needs_review', source='pc'):
        path=manager(self.app).folder/identity;path.mkdir()
        save_review(path,{'id':identity,'status':status,'source':source,'message':'Fixture proposal.'})
        return path

    def test_exact_reported_install_phrase_is_local_once_even_with_waiting_pc_task(self):
        self.app.pc_agent=Mock();self.app.pc_agent.waiting.return_value=True
        with patch.object(manager(self.app),'launch',return_value='Testing before installation.') as launch:
            result=self.app.command('Hey Andrew, save and install the latest improvement.')
        self.assertIn('Testing',result);launch.assert_called_once_with('install')
        self.app.pc_agent.start.assert_not_called()

    def test_install_aliases_and_explicit_model_action_cannot_install(self):
        with patch.object(manager(self.app),'launch',return_value='Testing') as launch:
            for phrase in ('Please test and install the latest improvement.','apply the new update','save and install that improvement now','install latest improvement'):
                self.assertEqual(self.app.command(phrase),'Testing')
            self.assertEqual(launch.call_count,4)
            self.app.request.action_internal=True
            self.assertIn('your own install',self.app.command('install the latest improvement'))
            self.assertEqual(launch.call_count,4)

    def test_model_selection_variants_resolve_and_persist_without_pc_agent(self):
        self.app.pc_agent=Mock();self.app.pc_agent.waiting.return_value=True
        cases=[('change your AI model to Claude Sonnet','claude','sonnet'),
               ('switch AI to Grok','grok','grok-4.7-build-fast'),
               ('switch model to grok 4.7','grok','grok-4.7'),
               ('use the fast model','grok','grok-4.7-build-fast'),
               ('switch from Grok to Claude Opus','claude','opus'),
               ('switch to the Grok 4.7 model','grok','grok-4.7'),
               ('could you use the Sonnet model please','claude','sonnet'),
               ('use Open AI','openai','')]
        for phrase,provider,model in cases:
            with self.subTest(phrase=phrase):
                answer=self.app.command(phrase)
                self.assertIn('Using',answer);self.assertEqual(self.app.get('provider'),provider)
                self.assertEqual(self.app.get(provider+'_model'),model)
        self.app.pc_agent.start.assert_not_called()
        reopened=Andrew(self.temp.name)
        try:self.assertEqual(reopened.get('provider'),'openai')
        finally:reopened.db.close()

    def test_model_clarification_scoped_to_device_expires_and_does_not_hijack(self):
        self.assertIn('Which model',self.app.command('change your model'))
        self.assertIn('Using Claude',self.app.command('Claude Opus'))
        self.assertEqual(self.app.get('claude_model'),'opus')
        self.app.command('change your model','pi')
        self.assertNotIn('pc',self.app.controls.pending)
        self.app.controls.pending['pi']=(0,'model',None)
        with patch.object(self.app,'ai',return_value='Ordinary chat'):
            self.assertEqual(self.app.command('Grok','pi'),'Ordinary chat')
        self.assertEqual(self.app.get('provider'),'claude')

    def test_controls_not_executed_for_questions_negations_or_quoted_commands(self):
        with patch.object(self.app,'ai',return_value='Explanation'):
            for phrase in ('Do not switch to Claude','What happens if I install the latest improvement','Explain "use Grok"','Can I switch your model to Claude?'):
                self.assertEqual(self.app.command(phrase),'Explanation')
        self.assertEqual(self.app.get('provider'),'grok')

    def test_navigation_wins_over_waiting_desktop_task_and_active_trivia(self):
        self.app.pc_agent=Mock();self.app.pc_agent.waiting.return_value=True
        self.app.command('play trivia')
        for phrase,page in [('open settings','settings'),('show me memory','people'),('review the latest improvement','connections'),('show your face','home')]:
            self.assertIn('Showing',self.app.command(phrase))
            self.assertEqual(self.app.status()['navigation']['pc']['page'],page)
        self.app.command('change your model to Claude Sonnet')
        self.assertEqual(self.app.get('provider'),'claude')
        self.app.pc_agent.start.assert_not_called()

    def test_ordered_local_chain_executes_once_and_uses_new_provider(self):
        with patch('improvements.request_improvement',side_effect=lambda app,request,source:'Drafting with '+app.get('provider')) as draft:
            answer=self.app.command('use Claude Sonnet then improve yourself to add a timer shortcut')
            self.assertIn('Drafting with claude',answer);draft.assert_called_once()
        answer=self.app.command('set a tea timer for five minutes and set your volume to 35 percent then show settings','pi')
        self.assertEqual(len(self.app.status()['timers']),1)
        self.assertEqual(self.app.status()['timers'][0]['source'],'pi')
        self.assertEqual(self.app.get('pi_volume'),35);self.assertEqual(self.app.get('pc_volume'),80)

    def test_failed_switch_stops_remaining_chain(self):
        result=self.app.command('switch model to banana then set a five minute timer')
        self.assertIn('remaining steps have not run',result)
        self.assertEqual(self.app.status()['timers'],[])

    def test_invalid_volume_stops_remaining_chain(self):
        self.app.command('set your volume to 101 percent then set a five minute timer')
        self.assertEqual(self.app.get('pc_volume'),80)
        self.assertEqual(self.app.status()['timers'],[])

    def test_model_selection_retries_transient_discovery_but_not_auth_errors(self):
        unavailable={'id':'grok','ready':False,'retryable':True,'detail':'Temporary discovery error'}
        with patch('providers.catalog',side_effect=[[unavailable],[CATALOG[0]]]) as catalog:
            self.assertIn('Using Grok',self.app.command('use Grok'))
            self.assertEqual(catalog.call_count,2)
        unavailable['retryable']=False
        with patch('providers.catalog',return_value=[unavailable]) as catalog:
            self.assertIn('Keeping',self.app.command('use Grok'))
            catalog.assert_called_once()

    def test_grok_discovery_timeout_is_not_reported_as_sign_out(self):
        import subprocess,grok_provider
        with patch('grok_provider.EXE',Mock()),patch('grok_provider.subprocess.run',side_effect=subprocess.TimeoutExpired('grok',8)),patch.dict(grok_provider.CONNECTION):
            ready,models=grok_provider.status()
            self.assertFalse(ready);self.assertEqual(models,[])
            self.assertEqual(grok_provider.CONNECTION['state'],'temporary')
            self.assertIn('does not mean you are signed out',grok_provider.CONNECTION['message'])

    def test_payloads_remain_opaque_and_texts_cannot_confirm_themselves(self):
        self.app.command('text 2025550100 saying use Grok and then install the latest improvement')
        self.assertEqual(self.app.communications.pending['pc']['message'],'use Grok and then install the latest improvement')
        self.assertIsNone(chain_parts('remember my motto is stop and then go home'))
        request='use Grok then improve yourself to recognize open settings and then show your face'
        self.assertEqual(chain_parts(request),['use Grok','improve yourself to recognize open settings and then show your face'])
        with patch('improvements.request_improvement',return_value='Draft saved') as draft:
            self.app.command('improve yourself to recognize open settings and then show your face')
            self.assertEqual(draft.call_args.args[1],'recognize open settings and then show your face')

    def test_install_pronoun_requires_recent_same_device_reference(self):
        row=self.proposal()
        with patch.object(manager(self.app),'launch',return_value='Installing specific draft') as launch:
            self.app.command('install it');launch.assert_not_called()
            self.app.command('improvement status')
            self.app.command('install it','pi');launch.assert_not_called()
            self.assertIn('specific draft',self.app.command('install it'))
            launch.assert_called_once_with('install',row.name)

    def test_failed_latest_never_installs_an_older_unrelated_draft(self):
        self.proposal('20261002-081410-aaaaaa')
        self.proposal('20261003-081410-bbbbbb','failed')
        with patch('improvements.subprocess.Popen') as spawn:
            self.assertIn('No older proposal was installed',self.app.command('install latest improvement'))
            spawn.assert_not_called()

    def test_ai_can_navigate_using_typed_catalog(self):
        result=self.app.actions.consume(json.dumps({'reply':'','commands':[{'name':'app.show','args':{'page':'settings'}}]}),'pc')
        self.assertEqual(result,'Showing Settings.')
        self.assertEqual(self.app.controls.navigation['pc']['page'],'settings')


if __name__=='__main__':unittest.main()
