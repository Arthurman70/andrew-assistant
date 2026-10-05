import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from core import ROOT,Andrew
from improvements import (ImprovementManager,stage_response,snapshot,save_review,read_json,update,editable,source_paths,check_source)
from improvement_installer import execute,run_tests
from code_map import cached_index,context


class AutoImprovementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests');self.root=Path(self.tmp.name)
        (self.root/'core.py').write_text('VALUE = 1\n')
        self.app=Mock();self.app.directory=self.root/'data'
        self.mgr=ImprovementManager(self.app,self.root)
    def tearDown(self):self.tmp.cleanup()
    def propose(self,value=2):return json.dumps({'summary':'change value','files':{},'edits':[{'path':'core.py','old':'VALUE = 1','new':f'VALUE = {value}'}]})
    def request(self,auto=True):
        with patch('providers.resolve',return_value=('grok','grok-selected',{'label':'Grok'})):
            result=self.mgr.request('Change value to two',auto_install=auto)
        self.mgr.thread.join(10);self.assertFalse(self.mgr.thread.is_alive());return result

    def test_every_application_area_is_editable_private_files_are_not(self):
        for name in ('server.py','wake_capture.py','pc_audio.py','improvements.py','improvement_installer.py',
                     'assets/upgrade.js','assets/upgrade.css','web/login.html','pi/agent.py',
                     'installers/install-pi.sh','Start-Andrew.ps1','android/app/src/main/java/org/andrewassistant/app/MainActivity.java','tests/test_core.py'):
            self.assertTrue(editable(name),name)
        for name in ('data/settings.json','data/camera.py','runtime/model.py','../core.py','pi/../../../token.py',
                     'assets/private/config.py','C:/secret.py','assets\\..\\data\\config.py'):
            self.assertFalse(editable(name),name)

    def test_auto_request_starts_installer_without_review_or_second_command(self):
        self.app.ai.side_effect=['{"paths":["core.py"]}',self.propose()]
        def install(path,action):
            self.assertEqual(action,'install');self.assertEqual(read_json(path/'review.json')['status'],'testing')
            (self.root/'core.py').write_bytes((path/'candidate/core.py').read_bytes())
            update(path,status='installed',live_files_changed=True)
        with patch.object(self.mgr,'install_watch',side_effect=install) as worker:
            answer=self.request();worker.assert_called_once()
        self.assertIn('automatically',answer);self.assertEqual((self.root/'core.py').read_text(),'VALUE = 2\n')
        self.assertEqual(self.mgr.items()[0]['status'],'installed')

    def test_preview_still_waits_for_explicit_install(self):
        self.app.ai.side_effect=['{"paths":["core.py"]}',self.propose()]
        with patch.object(self.mgr,'install_watch') as worker:self.request(False);worker.assert_not_called()
        self.assertEqual(self.mgr.items()[0]['status'],'needs_review');self.assertEqual((self.root/'core.py').read_text(),'VALUE = 1\n')

    def test_failed_tests_trigger_one_corrected_patch_with_same_model(self):
        self.app.ai.side_effect=['{"paths":["core.py"]}',self.propose(999),self.propose(2)]
        calls=[]
        def process(path,action):
            calls.append(action)
            if len(calls)==1:
                (path/'test-work').mkdir();(path/'tests.log').write_text('AssertionError: expected two, received 999')
                update(path,status='failed',message='Regression tests failed. No live files changed.')
            else:
                (self.root/'core.py').write_bytes((path/'candidate/core.py').read_bytes())
                update(path,status='installed',message='Installed',live_files_changed=True)
            p=Mock();p.pid=12;return p
        with patch.object(self.mgr,'installer_process',side_effect=process):self.request()
        self.assertEqual(calls,['install','install']);self.assertEqual(self.mgr.items()[0]['repair_attempts'],1)
        self.assertEqual((self.root/'core.py').read_text(),'VALUE = 2\n')
        self.assertIn('expected two',self.app.ai.call_args_list[-1].args[0])
        for call in self.app.ai.call_args_list:self.assertEqual(call.args[1:],('grok','grok-selected'))

    def test_installer_exit_cannot_leave_fake_testing_success(self):
        self.app.ai.side_effect=['{"paths":["core.py"]}',self.propose()]
        p=Mock();p.pid=12
        with patch.object(self.mgr,'installer_process',return_value=p):self.request()
        self.assertEqual(self.mgr.items()[0]['status'],'failed');self.assertEqual((self.root/'core.py').read_text(),'VALUE = 1\n')

    def test_install_during_drafting_means_install_when_ready(self):
        job=self.mgr.folder/'20261005-000000-abcdef';job.mkdir()
        save_review(job,{'id':job.name,'status':'drafting','auto_install':False})
        self.assertIn('already underway',self.mgr.launch('install',job.name))
        self.assertTrue(read_json(job/'review.json')['auto_install'])

    def test_small_patch_limits_prevent_large_rewrites(self):
        for files in ({f'feature_{i}.py':'VALUE = 2\n' for i in range(5)},
                      {'feature_large.py':'VALUE = 1\n'*301}):
            with self.assertRaises(ValueError):stage_response(self.root,self.root/'job',json.dumps({'files':files}))
        self.assertFalse((self.root/'job/candidate').exists())

    def test_html_with_local_assets_can_be_changed_and_js_is_syntax_checked(self):
        check_source('app.html','<html><script src="/assets/upgrade.js"></script></html>')
        with self.assertRaises(ValueError):check_source('app.html','<html><script src="https://evil.example/app.js"></script></html>')
        with self.assertRaises(ValueError):check_source('assets/new.js','function broken(')

    def test_map_cache_parses_only_changed_files_and_focuses_selected_method(self):
        text='"""Command router"""\nclass Router:\n    def selected(self):\n        return 1\n    def unrelated(self):\n        return '+repr('x'*13000)+'\n'
        (self.root/'core.py').write_text(text)
        cache=self.root/'data/map.json';index=cached_index(self.root,cache)
        with patch('code_map.file_index',side_effect=AssertionError('Unchanged file reparsed')),patch('code_map.ast.parse',side_effect=AssertionError('Unchanged imports reparsed')):
            cached_index(self.root,cache)
        (self.root/'core.py').write_text(text.replace('return 1','return 2'))
        updated=cached_index(self.root,cache)
        self.assertNotEqual(updated['core.py']['hash'],index['core.py']['hash'])
        ctx=context(self.root,['core.py::Router.selected'],index,'improve selected')
        self.assertIn('def selected',ctx['core.py']);self.assertNotIn('def unrelated',ctx['core.py'])
        self.assertLess(len(ctx['core.py']),1000)

    def test_real_test_copy_includes_login_page_and_assets(self):
        snapshot_names=source_paths(ROOT)
        self.assertIn('web/login.html',snapshot_names);self.assertIn('assets/browser.js',snapshot_names)
        self.assertIn('assets/andrew.png',snapshot_names)

    def test_original_test_cannot_be_weakened_to_accept_bad_app_code(self):
        tests=self.root/'tests';tests.mkdir()
        original='import unittest\nfrom core import VALUE\nclass Checks(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(VALUE,1)\n'
        (tests/'test_original.py').write_text(original)
        job=self.root/'job';job.mkdir();save_review(job,{'snapshot_hashes':snapshot(self.root,job)})
        stage_response(job/'base',job,json.dumps({'files':{'core.py':'VALUE = 999\n','tests/test_original.py':original.replace('VALUE,1','VALUE,999')}}))
        review=read_json(job/'review.json');actual_run=subprocess.run
        def run(command,**kwargs):return actual_run([sys.executable]+command[1:],**kwargs)
        with patch('improvement_installer.subprocess.run',side_effect=run),self.assertRaisesRegex(ValueError,'Regression tests failed'):
            run_tests(self.root,job,review)
        self.assertEqual((self.root/'core.py').read_text(),'VALUE = 1\n')

    def test_explicit_function_request_skips_planning_and_uses_code_map(self):
        self.app.ai.return_value=self.propose()
        with patch('providers.resolve',return_value=('claude','sonnet',{'label':'Claude'})):
            self.mgr.request('In core.py change VALUE to two',auto_install=False)
        self.mgr.thread.join(5);self.assertFalse(self.mgr.thread.is_alive())
        self.assertEqual(self.app.ai.call_count,1);self.assertIn('Relevant code map',self.app.ai.call_args.args[0])
        self.assertEqual(self.mgr.items()[0]['status'],'needs_review')

    def test_working_updater_is_frozen_before_model_changes(self):
        self.mgr.root=ROOT
        job=self.root/'frozen';job.mkdir()
        with patch('improvements.subprocess.Popen',return_value=Mock()) as process:
            self.mgr.installer_process(job,'install')
        runner=next(job.glob('runner-*'))
        self.assertEqual((runner/'improvement_installer.py').read_bytes(),(ROOT/'improvement_installer.py').read_bytes())
        self.assertEqual((runner/'improvements.py').read_bytes(),(ROOT/'improvements.py').read_bytes())
        self.assertIn(str(ROOT.resolve()),process.call_args.args[0])
        self.assertTrue((job/'installer.log').exists())

    def test_live_data_is_not_in_map_or_snapshot(self):
        (self.root/'data/credentials.py').write_text('SECRET = 1\n')
        index=cached_index(self.root,self.root/'data/map.json')
        self.assertNotIn('data/credentials.py',index)
        self.assertNotIn('data/credentials.py',source_paths(self.root))

    def test_unsolicited_model_code_action_does_not_begin_improvement(self):
        app=Andrew(self.root/'real-data')
        try:
            with patch('improvements.request_improvement') as request:
                response=app.actions.execute('improvement.request',{'request':'Change everything'},'pc')
                request.assert_not_called();self.assertIn('ask explicitly',response)
            with patch('improvements.request_improvement',return_value='Started') as request:
                self.assertEqual(app.command('fix your code to improve the timer display'),'Started')
                request.assert_called_once()
        finally:app.db.close()

    def test_bare_improve_command_requests_one_small_fix(self):
        app=Andrew(self.root/'bare-data')
        try:
            with patch('improvements.request_improvement',return_value='Started') as request:
                self.assertEqual(app.command('improve yourself'),'Started')
                self.assertIn('one small useful',request.call_args.args[1])
        finally:app.db.close()
