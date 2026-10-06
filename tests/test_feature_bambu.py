import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import Mock,patch
from core import Andrew,ROOT
from bambu import Bambu,studio_config
from pc_control import bambu_click_allowed
from pc_agent import PCAgent

class BambuTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'data/tests').mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests');self.root=Path(self.temp.name)
        self.app=Andrew(self.root/'app');self.config=self.root/'studio';self.config.mkdir()
        self.exe=self.root/'bambu-studio.exe';self.exe.write_bytes(b'fixture')
        self.first=self.root/'Tea cup.3mf';self.second=self.root/'Tea plate.3mf'
        self.project(self.first);self.project(self.second)
        self.values={'recent_projects':{'001':str(self.first),'002':str(self.second)},'presets':{'machine':'A1 mini','process':'Standard','filaments':['PLA']},'access_code':{'private':'hidden-access-code'},'token':'private-token'}
        (self.config/'BambuStudio.conf').write_text(json.dumps(self.values)+'\nchecksum-footer')
        self.app.bambu=Bambu(self.app,self.config,self.exe);self.b=self.app.bambu
    def tearDown(self):self.app.db.close();self.temp.cleanup()
    def project(self,path,**extra):
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('Metadata/project_settings.config',json.dumps({'printer_settings_id':'A1 mini','layer_height':'0.2','sparse_infill_density':'15%','post_process':[],**extra}))
    def test_config_footer_and_project_profiles_do_not_expose_credentials(self):
        self.assertEqual(studio_config(self.config/'BambuStudio.conf')['presets']['machine'],'A1 mini')
        text=json.dumps(self.b.status());self.assertNotIn('private-token',text);self.assertNotIn('hidden-access-code',text);self.assertIn('A1 mini',text);self.assertFalse(self.b.status()['live_printer_connected'])
    def test_recent_numbers_and_stable_ids_resolve_the_same_project(self):
        rows=self.b.recent();self.assertEqual(self.b.resolve('project 1'),self.first.resolve());self.assertEqual(self.b.resolve(rows[0]['id']),self.first.resolve())
        self.assertEqual(self.b.resolve('Tea plate'),self.second.resolve())
    def test_ambiguous_project_and_non_model_files_are_not_opened(self):
        with self.assertRaisesRegex(ValueError,'Several projects match'):self.b.resolve('Tea')
        with self.assertRaises(ValueError):self.b.resolve(str(self.exe))
        self.assertIsNone(self.b.route('open my website project','pc'))
    def test_open_uses_existing_studio_and_literal_file_argument(self):
        with patch('bambu.subprocess.Popen') as launch:
            result=self.app.command('open Bambu project 2')
            self.assertIn('Tea plate',result);self.assertEqual(launch.call_args.args[0],[str(self.exe),str(self.second.resolve())])
    def test_speech_alias_opens_bambu_without_an_ai_round_trip(self):
        with patch('pc_control.PCController') as controller,patch.object(self.app,'ai',side_effect=AssertionError('No model needed')):
            controller.return_value.open.return_value={'opened':True}
            self.assertIn('Bambu Studio',self.app.command('open bamboo studio'));controller.return_value.open.assert_called_once_with('bambu')
    def test_open_studio_reuses_existing_window_without_spawning_duplicate(self):
        from pc_control import PCController
        controller=PCController.__new__(PCController);controller.windows=Mock(return_value=[{'app':'bambu-studio.exe','title':'Custom wx pane title','window':9}]);controller.select=Mock(return_value={'window':9});controller.window=Mock()
        with patch('pc_control.subprocess.Popen',side_effect=AssertionError('No duplicate')):self.assertTrue(controller.open('bambu')['opened'])
        controller.select.assert_called_once_with(9)
    def test_native_frame_fallback_finds_studio_when_wx_uia_says_offscreen(self):
        from pc_control import PCController
        controller=PCController.__new__(PCController);controller.desktop=Mock();controller.desktop.windows.return_value=[]
        frame={'window':9,'title':'Untitled - BambuStudio','app':'bambu-studio.exe'};controller.bambu_native_windows=Mock(return_value=[frame])
        self.assertEqual(controller.windows(),[frame]);controller.bambu_native_windows.assert_called_once()
    def test_inspection_returns_only_selected_settings_and_no_gcode_body(self):
        self.project(self.first,access_code='secret',machine_start_gcode='private-gcode')
        data=self.b.describe();self.assertEqual(data['settings']['layer_height'],'0.2');self.assertNotIn('secret',json.dumps(data));self.assertNotIn('private-gcode',json.dumps(data))
    def test_slice_preserves_source_and_builds_only_typed_local_cli_flags(self):
        original=self.first.read_bytes()
        with patch('bambu.threading.Thread') as thread:
            answer=self.b.slice('latest',plate=1,overrides={'infill':20,'supports':True})
            command=thread.call_args.kwargs['args'][0]
            self.assertEqual(command[0],str(self.exe));self.assertIn('--slice',command);self.assertIn('--sparse-infill-density',command);self.assertIn('20%',command);self.assertNotIn('--no-check',command)
            self.assertNotEqual(command[-2],str(self.first));self.assertEqual(self.first.read_bytes(),original);self.assertIn('does not start a print',answer)
    def test_postprocessing_scripts_and_arbitrary_flags_cannot_execute(self):
        self.project(self.first,post_process=['malicious script'])
        with patch('bambu.subprocess.Popen',side_effect=AssertionError('No script')):
            with self.assertRaisesRegex(ValueError,'post-processing'):self.b.slice()
        self.project(self.first)
        with self.assertRaises(ValueError):self.b.slice(overrides={'post_process':'anything'})
        with self.assertRaises(ValueError):self.b.slice(overrides={'infill':101})
    def test_voiced_plate_number_is_passed_to_slicer(self):
        with patch.object(self.b,'slice',return_value='slicing') as slicer:
            self.assertEqual(self.app.command('slice the latest Bambu project on plate 2'),'slicing');slicer.assert_called_once_with('latest','pc',2)
    def test_latest_project_details_work_with_natural_bambu_word_order(self):
        with patch.object(self.app,'ai',side_effect=AssertionError('No model needed')):
            answer=self.app.command('inspect the latest Bambu project')
        self.assertIn('Tea cup.3mf',answer);self.assertIn('15%',answer)
    def test_cancellation_only_terminates_andrews_slicing_process(self):
        self.b.job={'state':'running'};self.b.proc=Mock();self.b.proc.poll.return_value=None
        self.assertIn('cancelled',self.b.cancel_slice());self.b.proc.terminate.assert_called_once()
    def test_worker_requires_nonempty_gcode_before_success(self):
        folder=self.root/'job';folder.mkdir();output=folder/'result.3mf';self.project(output)
        self.b.job={'state':'running'};self.b.running=True
        process=Mock();process.wait.return_value=0;process.poll.return_value=0
        with patch('bambu.subprocess.Popen',return_value=process):self.b.slice_worker(['fixture'],folder,output,'pc')
        self.assertEqual(self.b.job['state'],'failed');self.assertFalse(self.b.running)
        with zipfile.ZipFile(output,'a') as archive:archive.writestr('Metadata/plate_1.gcode','G1 X1 Y1')
        self.b.job={'state':'running'};self.b.notify=Mock()
        with patch('bambu.subprocess.Popen',return_value=process):self.b.slice_worker(['fixture'],folder,output,'pc')
        self.assertEqual(self.b.job['state'],'complete');self.b.notify.assert_called_once()
    def test_device_status_is_read_from_observed_studio_not_saved_profile(self):
        controller=Mock();controller.windows.return_value=[{'app':'bambu-studio.exe','window':123}]
        controller.select.return_value={'elements':[{'name':'Device','actionable':True,'ref':'fresh'}]}
        controller.click.return_value={'elements':[{'name':t,'role':'Text'} for t in ['Printing Progress','Center.stl','100','%','Layer: 800/800','Finished']]}
        with patch('pc_control.PCController',return_value=controller):answer=self.app.command('printer status')
        self.assertIn('finished',answer);self.assertIn('100 percent',answer);controller.click.assert_called_once_with('fresh')
    def test_unreadable_device_status_uses_existing_desktop_task_route(self):
        self.app.pc_agent=Mock();self.app.pc_agent.start.return_value='checking'
        with patch.object(self.b,'printer_status',return_value=None):self.assertEqual(self.app.command('printer status'),'checking')
        self.assertIn('Bambu Studio',self.app.pc_agent.start.call_args.args[0])
    def test_model_catalog_uses_same_bambu_commands(self):
        from assistant_actions import COMMANDS
        self.assertIn('bambu.command',COMMANDS)
        answer=self.app.actions.consume(json.dumps({'reply':'','commands':[{'name':'bambu.command','args':{'command':'Bambu profiles'}}]}),'pc')
        self.assertIn('A1 mini',answer)
    def test_pc_agent_prefers_direct_project_tool_without_native_ui(self):
        plan=iter([{'action':'bambu','args':{'action':'inspect','project':'latest'}},{'action':'finish','status':'complete','answer':'Project inspected.'}])
        agent=PCAgent(self.app,planner=lambda *a:json.dumps(next(plan)),controller_factory=lambda:(_ for _ in ()).throw(AssertionError('No native UI')))
        agent.run('Inspect my latest Bambu project','pc');self.assertEqual(agent.status()['state'],'complete')
    def test_wx_studio_tabs_are_actionable_but_status_does_not_authorize_a_print(self):
        self.assertTrue(bambu_click_allowed('Pane','Device','printer status'))
        self.assertFalse(bambu_click_allowed('Button','Print plate','how is my print doing'))
        self.assertFalse(bambu_click_allowed('Button','Print plate','slice a project and do not print'))
        self.assertTrue(bambu_click_allowed('Button','Print plate','print this project'))
        self.assertFalse(bambu_click_allowed('Pane','Update','update firmware'))
        self.assertFalse(bambu_click_allowed('Button','Camera','printer status'))
        self.assertFalse(bambu_click_allowed('Button','Camera','is the camera on'))
        self.assertTrue(bambu_click_allowed('Button','Camera','show my printer camera'))
        self.assertFalse(bambu_click_allowed('Pane','Pause','do not pause my print'))
        self.assertTrue(bambu_click_allowed('Pane','Pause','pause my printer'))

if __name__=='__main__':unittest.main()
