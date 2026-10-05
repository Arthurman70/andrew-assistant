import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from core import ROOT
from improvements import stage_response, snapshot, save_review, read_json, ImprovementManager, route, json_response, ResponseFormatError
from improvement_installer import verify_base, verify_candidate, prepare_backup, install_files, restore_files, execute


class ImprovementTests(unittest.TestCase):
    def setUp(self):
        self.parent = ROOT / 'data/tests'
        self.parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.parent)
        self.destination = Path(self.temp.name)

    def tearDown(self):
        assert self.destination.resolve().is_relative_to(self.parent.resolve())
        self.temp.cleanup()

    def test_protected_files_and_traversal_rejected(self):
        for name in ('../core.py','data/relay-token.txt','runtime/wake/config.json',
                     'C:/evil.html','assets/../../data/relay-token.txt','assets/private/key.py'):
            with self.assertRaises(ValueError):
                stage_response(ROOT, self.destination, json.dumps({'files': {name: '<html></html>'}}))

    def test_candidate_never_overwrites_live_display(self):
        before = (ROOT / 'dashboard.html').read_bytes()
        stage_response(ROOT, self.destination, json.dumps({'summary': 'test', 'files': {'dashboard.html': '<html>Candidate</html>'}}))
        self.assertEqual((ROOT / 'dashboard.html').read_bytes(), before)
        self.assertTrue((self.destination / 'candidate/dashboard.html').exists())
        self.assertEqual(json.loads((self.destination / 'review.json').read_text())['status'], 'needs_review')

    def test_wake_tuning_is_editable_but_bounded_and_cannot_change_privacy(self):
        from voice_tuning import DEFAULTS
        settings=dict(DEFAULTS,quiet_min_rms=35)
        stage_response(ROOT,self.destination,json.dumps({'files':{'wake_tuning.json':json.dumps(settings)}}))
        self.assertEqual(json.loads((self.destination/'candidate/wake_tuning.json').read_text()),settings)
        for invalid in (dict(settings,keyword_threshold=0),dict(settings,quiet_min_rms=True),
                        dict(settings,transcribe_background=True),dict(settings,keyword_score=float('inf'))):
            with self.subTest(settings=invalid),self.assertRaises(ValueError):
                stage_response(ROOT,self.destination,json.dumps({'files':{'wake_tuning.json':json.dumps(invalid)}}))

    def test_wake_request_gets_tuning_source_even_when_model_selects_core(self):
        from voice_tuning import DEFAULTS
        root=self.destination/'wake-app';root.mkdir()
        (root/'core.py').write_text('VALUE = 1\n')
        (root/'wake_tuning.json').write_text(json.dumps(DEFAULTS))
        app=Mock();app.directory=root/'data'
        app.ai.side_effect=[json.dumps({'paths':['core.py']}),json.dumps({'summary':'tune pickup',
            'edits':[],'files':{'wake_tuning.json':json.dumps(dict(DEFAULTS,quiet_min_rms=35))}})]
        mgr=ImprovementManager(app,root)
        with patch('providers.resolve',return_value=('grok','grok-selected',{'label':'Grok'})):
            mgr.request('Make voice detection a little easier','pc','grok','grok-selected',auto_install=False)
        mgr.thread.join(5);self.assertFalse(mgr.thread.is_alive())
        prompt=app.ai.call_args_list[1].args[0]
        self.assertIn('"wake_tuning.json":',prompt)
        self.assertEqual(mgr.items()[0]['status'],'needs_review')

    def fixture(self):
        root=self.destination/'app';root.mkdir()
        (root/'core.py').write_text('VALUE = 1\n',encoding='utf-8')
        path=self.destination/'job';path.mkdir()
        save_review(path,{'id':'20260930-000000-abcdef','snapshot_hashes':snapshot(root,path)})
        stage_response(path/'base',path,json.dumps({'summary':'change value','edits':[
            {'path':'core.py','old':'VALUE = 1','new':'VALUE = 2'}]}))
        return root,path,read_json(path/'review.json')

    def test_alarm_request_always_includes_schema_tick_and_feature_dependencies(self):
        root=self.destination/'alarm-app';root.mkdir()
        for name,content in {'core.py':'VALUE = 1\n','feature_timers.py':'VALUE = 1\n',
                             'feature_alarm_recurrence.py':'VALUE = 1\n','local_commands.py':'VALUE = 1\n'}.items():
            (root/name).write_text(content)
        for provider in ('openai','claude','grok','local'):
            with self.subTest(provider=provider):
                app=Mock();app.directory=root/('data-'+provider)
                app.ai.side_effect=[json.dumps({'paths':['local_commands.py']}),json.dumps({'summary':'weekly alarms',
                    'edits':[{'path':'feature_timers.py','old':'VALUE = 1','new':'VALUE = 2'}],'files':{}})]
                mgr=ImprovementManager(app,root)
                with patch('providers.resolve',return_value=(provider,'selected',{'label':provider})):
                    mgr.request('Set alarms for a whole week recurring','pc',provider,'selected',auto_install=False)
                mgr.thread.join(5);self.assertFalse(mgr.thread.is_alive())
                prompt=app.ai.call_args_list[1].args[0]
                for name in ('core.py','feature_timers.py','feature_alarm_recurrence.py'):self.assertIn('"'+name+'":',prompt)
                self.assertEqual(mgr.items()[0]['status'],'needs_review')
                self.assertEqual((root/'feature_timers.py').read_text(),'VALUE = 1\n')
                for call in app.ai.call_args_list:self.assertEqual(call.args[1:],(provider,'selected'))

    def test_missing_source_no_change_is_recovered_with_editable_file(self):
        root=self.destination/'dependency-app';root.mkdir()
        (root/'core.py').write_text('VALUE = 1\n');(root/'feature_preferences.py').write_text('VALUE = 1\n')
        app=Mock();app.directory=root/'data'
        app.ai.side_effect=[json.dumps({'paths':['core.py']}),json.dumps({'summary':
            'Unsupported with the supplied source: feature_preferences.py is missing.','edits':[],'files':{}}),
            json.dumps({'summary':'saved preferences','edits':[{'path':'feature_preferences.py','old':'VALUE = 1','new':'VALUE = 2'}],'files':{}})]
        mgr=ImprovementManager(app,root)
        with patch('providers.resolve',return_value=('openai','gpt-6-luna',{'label':'OpenAI'})):
            mgr.request('Save my preferences',auto_install=False)
        mgr.thread.join(5);self.assertFalse(mgr.thread.is_alive())
        self.assertEqual(mgr.items()[0]['status'],'needs_review');self.assertEqual(app.ai.call_count,3)
        self.assertIn('"feature_preferences.py":',app.ai.call_args_list[2].args[0])
        self.assertEqual((root/'feature_preferences.py').read_text(),'VALUE = 1\n')

    def test_false_missing_source_claim_is_retried_once_not_installed(self):
        nochange=json.dumps({'summary':'Unsupported: supplied source does not include core.py or the SQLite schema.',
                             'edits':[],'files':{}})
        root,app,mgr,row=self.generate_responses(['{"paths":["core.py"]}',nochange,nochange])
        self.assertEqual(app.ai.call_count,3);self.assertEqual(row['status'],'no_changes')
        self.assertIn('required editable source is supplied',app.ai.call_args_list[2].args[0])
        self.assertEqual((root/'core.py').read_text(),'VALUE = 1\n')

    def test_code_edits_stage_without_executing_or_touching_live_code(self):
        root,path,review=self.fixture()
        self.assertEqual((root/'core.py').read_text(),'VALUE = 1\n')
        self.assertEqual((path/'candidate/core.py').read_text(),'VALUE = 2\n')
        self.assertIn('core.py',review['candidate_hashes'])

    def test_invalid_syntax_and_nonunique_edits_do_not_stage(self):
        with self.assertRaisesRegex(ValueError,'STRING'):
            stage_response(ROOT,self.destination,json.dumps({'files':{'core.py':{'patch':'VALUE = 2'}},
                'edits':[{'path':'core.py','old':'VALUE = 1','new':'VALUE = 2'}]}))
        with self.assertRaises(SyntaxError):
            stage_response(ROOT,self.destination,json.dumps({'files':{'feature_bad.py':'def broken('}}))
        self.assertFalse((self.destination/'candidate').exists())
        with self.assertRaises(ValueError):
            stage_response(ROOT,self.destination,json.dumps({'edits':[{'path':'core.py','old':'DOES NOT EXIST','new':'no'}]}))

    def test_stale_source_or_tampered_candidate_blocks_install(self):
        root,path,review=self.fixture()
        verify_base(root,review);verify_candidate(path,review)
        (root/'core.py').write_text('VALUE = 3\n')
        with self.assertRaises(ValueError): verify_base(root,review)
        (path/'candidate/core.py').write_text('VALUE = 4\n')
        with self.assertRaises(ValueError): verify_candidate(path,review)

    def test_redundant_small_model_file_and_edit_must_agree(self):
        root,path,review=self.fixture()
        value={'files':{'core.py':{'new':'VALUE = 2\n'}},
               'edits':[{'path':'core.py','old':'VALUE = 1','new':'VALUE = 2'}]}
        stage_response(root,path,json.dumps(value))
        self.assertEqual((path/'candidate/core.py').read_text(),'VALUE = 2\n')
        value['files']['core.py']['new']='VALUE = 3\n'
        with self.assertRaisesRegex(ValueError,'disagree'):stage_response(root,path,json.dumps(value))

    def test_backup_restores_exact_previous_bytes(self):
        root,path,review=self.fixture()
        before=(root/'core.py').read_bytes()
        prepare_backup(root,path,review);install_files(root,path,review)
        self.assertNotEqual((root/'core.py').read_bytes(),before)
        restore_files(root,path,review)
        self.assertEqual((root/'core.py').read_bytes(),before)

    def test_test_failure_never_stops_server_or_installs(self):
        root,path,review=self.fixture()
        with patch('improvement_installer.run_tests',side_effect=ValueError('Regression tests failed')), \
             patch('improvement_installer.stop_server') as stop:
            execute('install',path,123,root)
        stop.assert_not_called()
        self.assertEqual((root/'core.py').read_text(),'VALUE = 1\n')
        self.assertEqual(read_json(path/'review.json')['status'],'failed')

    def test_startup_failure_rolls_back_and_restarts_previous_version(self):
        root,path,review=self.fixture()
        proc=Mock();proc.poll.return_value=None
        with patch('improvement_installer.run_tests',return_value='Passed'), \
             patch('improvement_installer.stop_server'),patch('improvement_installer.time.sleep'), \
             patch('improvement_installer.stop_started'), \
             patch('improvement_installer.start_server',return_value=proc), \
             patch('improvement_installer.healthy',side_effect=[False,True]):
            execute('install',path,123,root)
        self.assertEqual((root/'core.py').read_text(),'VALUE = 1\n')
        self.assertEqual(read_json(path/'review.json')['status'],'rolled_back')

    def test_windows_launcher_child_is_accepted_as_healthy_server(self):
        from improvement_installer import healthy
        launcher=Mock();launcher.pid=10;launcher.poll.return_value=None
        actual=Mock();actual.pid=20;actual.parents.return_value=[launcher]
        with patch('improvement_installer.urllib.request.urlopen'), \
             patch('improvement_installer.json.load',return_value={'ready':True,'pid':20}), \
             patch('improvement_installer.psutil.Process',return_value=actual):
            self.assertTrue(healthy(launcher))

    def test_all_providers_keep_selected_model_across_every_generation_step(self):
        for provider,model in [('grok','grok-test'),('claude','opus'),('openai',''),('local','tiny:latest')]:
            with self.subTest(provider=provider):
                root=self.destination/provider;root.mkdir();(root/'core.py').write_text('VALUE = 1\n')
                app=Mock();app.directory=root/'data'
                app.ai.side_effect=[json.dumps({'paths':['core.py']}),json.dumps({'summary':'updated','edits':[
                    {'path':'core.py','old':'VALUE = 1','new':'VALUE = 2'}]})]
                mgr=ImprovementManager(app,root)
                with patch('providers.resolve',return_value=(provider,model,{'label':provider})):
                    mgr.request('Change the value','pi',provider,model,auto_install=False)
                mgr.thread.join(5)
                self.assertFalse(mgr.thread.is_alive())
                self.assertEqual(mgr.items()[0]['status'],'needs_review')
                for call in app.ai.call_args_list:
                    self.assertEqual(call.args[1:],(provider,model))
                    self.assertEqual(call.kwargs['purpose'],'improvement')
                    self.assertEqual(call.kwargs['response_schema']['type'],'object')

    def test_json_wrappers_preserve_the_complete_proposal(self):
        value={'summary':'one small edit','edits':[{'path':'core.py','old':'VALUE = 1','new':'VALUE = 2'}],'files':{}}
        raw=json.dumps(value)
        for response in (raw,'\ufeff'+raw,'```json\n'+raw+'\n```','Here is the proposal:\n'+raw,
                         'Proposed change:\n```json\n'+raw+'\n```\nPlease review it.'):
            self.assertEqual(json_response(response),value)

    def test_malformed_empty_or_ambiguous_json_is_not_guessed(self):
        for response in ('',None,"I'll look through the files.",'{"paths":["core.py"]',
                         '{"paths":[]} {"paths":["core.py"]}','{"paths":[],"paths":["core.py"]}',
                         '```json\n{"paths":[]}\n```\n```json\n{"paths":["core.py"]}\n```',
                         '[{"paths":["core.py"]}]','{"files":NaN}'):
            with self.subTest(response=response),self.assertRaises(ResponseFormatError):json_response(response)

    def generate_responses(self,responses):
        root=self.destination/'fixture';root.mkdir();(root/'core.py').write_text('VALUE = 1\n')
        app=Mock();app.directory=root/'data';app.ai.side_effect=responses
        mgr=ImprovementManager(app,root)
        with patch('providers.resolve',return_value=('grok','grok-selected',{'label':'Grok'})):
            mgr.request('Change the value','pc','grok','grok-selected',auto_install=False)
        mgr.thread.join(5);self.assertFalse(mgr.thread.is_alive())
        return root,app,mgr,mgr.items()[0]

    def test_non_json_planning_retries_and_then_stages_real_code(self):
        proposal=json.dumps({'summary':'updated','files':{},'edits':[{'path':'core.py','old':'VALUE = 1','new':'VALUE = 2'}]})
        root,app,mgr,row=self.generate_responses(["I'll look through the files.",'```json\n{"paths":["core.py"]}\n```',proposal])
        self.assertEqual(row['status'],'needs_review');self.assertEqual(app.ai.call_count,3)
        self.assertEqual((root/'core.py').read_text(),'VALUE = 1\n')
        self.assertIn('Do not announce future work',app.ai.call_args_list[1].args[0])
        for call in app.ai.call_args_list:self.assertEqual(call.args[1:],('grok','grok-selected'))
        review=read_json(mgr.get(row['id'])/'review.json')
        self.assertEqual(review['planning_attempts'],2)

    def test_planning_failure_is_bounded_and_explained_without_parser_trace(self):
        root,app,mgr,row=self.generate_responses(['I will inspect the files.','Still inspecting.'])
        self.assertEqual(row['status'],'failed');self.assertEqual(app.ai.call_count,2)
        self.assertIn('two attempts',row['message']);self.assertNotIn('Expecting value',row['message'])
        self.assertFalse((mgr.get(row['id'])/'candidate').exists())

    def test_draft_format_failure_retries_without_executing_model_text(self):
        proposal=json.dumps({'summary':'updated','files':{},'edits':[{'path':'core.py','old':'VALUE = 1','new':'VALUE = 2'}]})
        root,app,mgr,row=self.generate_responses(['{"paths":["core.py"]}','I will edit it now.',proposal])
        self.assertEqual(row['status'],'needs_review');self.assertEqual(app.ai.call_count,3)
        self.assertEqual(read_json(mgr.get(row['id'])/'review.json')['drafting_attempts'],2)

    def test_valid_no_change_response_does_not_claim_installation(self):
        root,app,mgr,row=self.generate_responses(['{"paths":["core.py"]}',json.dumps({
            'summary':'This feature is already available.','edits':[],'files':{}})])
        self.assertEqual(row['status'],'no_changes');self.assertFalse(row['live_files_changed'])
        self.assertIn('already available',row['message']);self.assertFalse((mgr.get(row['id'])/'candidate').exists())

    def test_wrapped_json_still_cannot_modify_protected_files(self):
        value=json.dumps({'files':{'data/config.py':'print("not allowed")'}})
        with self.assertRaisesRegex(ValueError,'protected'):
            stage_response(ROOT,self.destination,'Here is the proposal:\n```json\n'+value+'\n```')

    def test_duplicated_grok_final_object_is_one_proposal_not_two_edits(self):
        value={'summary':'updated','files':{},'edits':[{'path':'core.py','old':'VALUE = 1','new':'VALUE = 2'}]}
        response=json.dumps(value)
        self.assertEqual(json_response(response+response),value)
        root,app,mgr,row=self.generate_responses(['{"paths":["core.py"]}',response+response])
        self.assertEqual(row['status'],'needs_review')
        self.assertEqual((mgr.get(row['id'])/'candidate/core.py').read_text(),'VALUE = 2\n')

    def test_future_work_promise_is_retried_not_reported_as_no_change(self):
        promise=json.dumps({'summary':"I'll look through how Andrew handles alarms.",'edits':[],'files':{}})
        proposal=json.dumps({'summary':'updated','files':{},'edits':[{'path':'core.py','old':'VALUE = 1','new':'VALUE = 2'}]})
        root,app,mgr,row=self.generate_responses(['{"paths":["core.py"]}',promise+promise,proposal])
        self.assertEqual(row['status'],'needs_review');self.assertEqual(app.ai.call_count,3)
        self.assertIn('promises future work',read_json(mgr.get(row['id'])/'review.json')['last_validation_error'])

    def test_generated_tests_must_be_discoverable_by_the_actual_runner(self):
        bad='def test_alarm():\n    assert True\n'
        with self.assertRaisesRegex(ValueError,'unittest'):
            stage_response(ROOT,self.destination,json.dumps({'files':{'tests/test_feature_alarm.py':bad}}))
        good='import unittest\nclass AlarmTests(unittest.TestCase):\n    def test_alarm(self):\n        self.assertTrue(True)\n'
        stage_response(ROOT,self.destination,json.dumps({'files':{'tests/test_feature_alarm.py':good}}))
        self.assertTrue((self.destination/'candidate/tests/test_feature_alarm.py').is_file())

    def test_voice_override_and_install_are_local_not_model_actions(self):
        app=Mock()
        with patch('improvements.request_improvement',return_value='started') as request:
            self.assertEqual(route(app,'improve yourself using Claude Sonnet to make timers clearer','pi'),'started')
            request.assert_called_once_with(app,'make timers clearer','pi','claude','Sonnet')
        with patch('improvements.manager') as mgr:
            route(app,'install the latest improvement','pi')
            mgr.return_value.launch.assert_called_once_with('install')
