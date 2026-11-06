import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class StartupTests(unittest.TestCase):
    def setup_capture(self, rename=False):
        source=ast.parse((Path(__file__).resolve().parents[1]/'pi/agent.py').read_text())
        node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='capture')
        config={'name':'Andrew','listening':True,'microphone_device':'auto'}
        sequence=[];state={}
        def ready(**kwargs):config['name']='Tom';sequence.append('configuration')
        detector=Mock(active=False,phase='waiting for name',rejected_wakes=0,empty_wakes=0,cautious=False)
        detector.background.frames=150;detector.feed.return_value=None
        detector.detector.engine='local keyword grammar'
        def wake(path,name):sequence.append(('wake',name));return detector
        control=Mock();control.feed.return_value=None
        def interrupt(path,name):sequence.append(('interrupt',name));return control
        process=Mock()
        def read(size):
            if rename:config['name']='Charlie'
            return bytes(640)
        process.stdout.read.side_effect=[read(640)] if rename else [bytes(640),b'']
        # Apply rename when the audio arrives, not before capture initialization.
        if rename:
            config['name']='Andrew';process.stdout.read.side_effect=read
        def open_mic(*args,**kwargs):sequence.append('microphone');return process
        env={'STATE':state,'CONFIG':config,'CONFIG_READY':SimpleNamespace(wait=ready),
             'microphone':lambda:'plughw:Camera,0','WakeCapture':wake,'InterruptDetector':interrupt,
             'Path':Path,'__file__':'/opt/andrew/agent.py',
             'subprocess':SimpleNamespace(Popen=open_mic,PIPE=-1,TimeoutExpired=TimeoutError),
             'snoozed':lambda:False,'PLAY_LOCK':SimpleNamespace(locked=lambda:False),
             'REQUEST_BUSY':SimpleNamespace(is_set=lambda:False),'SPEECH_STATE':{'paused':None},
             'PAUSED':SimpleNamespace(is_set=lambda:False),'BUSY':SimpleNamespace(is_set=lambda:False),
             'array':lambda code,frame:[0]*320,'wake_phrase':lambda name:'Hey '+name}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'capture-test','exec'),env)
        return env['capture'],sequence,state,process,detector,control

    def test_current_name_models_are_ready_before_opening_microphone(self):
        capture,sequence,state,process,_,_=self.setup_capture()
        with self.assertRaisesRegex(RuntimeError,'Microphone disconnected'):capture()
        self.assertEqual(sequence,['configuration',('wake','Tom'),('interrupt','Tom'),'microphone'])
        self.assertEqual(state['wake_engine'],'local keyword grammar')
        process.terminate.assert_called_once()

    def test_rename_closes_stream_instead_of_reloading_models_during_capture(self):
        capture,_,_,process,detector,interrupt=self.setup_capture(rename=True)
        capture()
        detector.feed.assert_not_called();interrupt.feed.assert_not_called()
        process.terminate.assert_called_once()


if __name__=='__main__':unittest.main()
