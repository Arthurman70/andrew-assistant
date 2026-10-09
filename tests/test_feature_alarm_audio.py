import io
import ast,json,types
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
import wave
import numpy as np
from core import Andrew,ROOT
from alarm_audio import AlarmAudio,chime,private_clip
from code_map import cached_index,context
from improvements import source_bundle
from speaker_player import SpeakerPlayer


class AlarmTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests');self.root=Path(self.temp.name);self.app=Andrew(self.root/'data');self.clock=10
        self.audio=AlarmAudio(self.app,self.root/'Downloads',lambda:self.clock);self.app.alarm_audio=self.audio
    def tearDown(self):self.app.db.close();self.temp.cleanup()
    def alarm(self,source='pc'):
        row=self.app.schedule.create('Wake up',1,'alarm',source);self.app.due(row['due']+.01);return row
    def frame(self,source='pc'):self.audio.sync();return self.audio.frames[source]
    def test_alarm_has_non_silent_pcm_and_does_not_use_tts(self):
        self.alarm();dispatch=Mock(return_value=True);self.audio.dispatch=dispatch;self.audio.tick()
        content=dispatch.call_args.args[0]
        with wave.open(io.BytesIO(content)) as w:self.assertEqual(w.getsampwidth(),2);self.assertGreater(np.max(np.abs(np.frombuffer(w.readframes(w.getnframes()),dtype='<i2'))),1000)
        dispatch.assert_called_once();self.assertTrue(self.frame()['key'])
    def test_repeats_with_quiet_gaps_until_dismissed(self):
        self.alarm();self.audio.dispatch=Mock(return_value=True);self.audio.tick();self.clock+=4;self.audio.tick();self.assertEqual(self.audio.dispatch.call_count,1)
        self.clock+=4;self.audio.tick();self.assertEqual(self.audio.dispatch.call_count,2)
        event=self.frame()['cancel'];self.app.command('dismiss alarm number one');self.assertTrue(event.is_set());self.assertFalse(self.frame()['key']);self.clock+=10;self.audio.tick();self.assertEqual(self.audio.dispatch.call_count,2)
    def test_snooze_stops_current_and_queued_clips_then_rings_again(self):
        row=self.alarm();old=self.frame()['cancel'];oldkey=self.frame()['key'];self.app.command('snooze alarm number one for five minutes');self.assertTrue(old.is_set());self.assertFalse(self.frame()['key'])
        row=self.app.schedule.rows('alarm')[0];self.app.due(row['due']+.01);self.assertTrue(self.frame()['key']);self.assertNotEqual(self.frame()['key'],oldkey)
    def test_pc_pi_browser_stay_on_the_requesting_device(self):
        self.alarm('pi');self.assertTrue(self.frame('pi')['key']);self.assertFalse(self.frame('pc')['key'])
        self.audio.dispatch=Mock(return_value=True);self.audio.tick();self.audio.dispatch.assert_not_called()
        self.app.request.browser_device='browser-device-one';self.alarm('browser');self.assertEqual(self.frame('browser')['alarms'][0]['device'],'browser-device-one')
    def test_pc_speech_off_does_not_mute_alarm_clips(self):
        self.app.set('pc_speech',False);self.alarm();self.audio.dispatch=Mock(return_value=True);self.audio.tick();self.audio.dispatch.assert_called_once()
    def test_multiple_alarms_cancel_independently(self):
        a=self.alarm();b=self.alarm();old=self.frame()['cancel'];self.app.command('cancel alarm number one');self.assertTrue(old.is_set());self.assertEqual([x['id'] for x in self.frame()['alarms']],[b['id']])
    def test_silence_is_per_device_and_next_occurrence_is_not_muted(self):
        row=self.alarm();self.alarm('pi');self.audio.silence('pc');self.assertFalse(self.frame()['key']);self.assertTrue(self.frame('pi')['key']);self.audio.resume('pc');self.assertTrue(self.frame()['key'])
        self.audio.silence('pc');self.audio.begin(row);self.assertTrue(self.frame()['key'])
    def test_busy_dispatch_retries_without_losing_alarm(self):
        self.alarm();self.audio.dispatch=Mock(side_effect=[False,True]);self.audio.tick();self.audio.tick();self.assertEqual(self.audio.dispatch.call_count,2)
    def test_restarted_manager_recovers_ringing_alarms(self):
        self.alarm();new=AlarmAudio(self.app,self.root/'Downloads');new.sync();self.assertTrue(new.frames['pc']['key'])
    def test_old_one_shot_alarm_is_preserved_but_not_replayed_on_upgrade(self):
        row=self.alarm()
        with self.app.lock,self.app.db:self.app.db.execute('UPDATE timers SET due=? WHERE id=?',(time.time()-172800,row['id']))
        new=AlarmAudio(self.app,self.root/'Downloads');new.sync();self.assertFalse(new.frames['pc']['key']);self.assertEqual(self.app.schedule.rows('alarm')[0]['status'],'ringing')
        new.resume('pc');self.assertTrue(new.frames['pc']['key'])
    def test_timers_and_reminders_remain_notifications(self):
        row=self.app.schedule.create('Tea',1,'timer');self.app.due(row['due']+.01);self.assertFalse(self.frame()['key'])
    def test_cinematic_selection_stays_private_and_falls_back_if_deleted(self):
        folder=self.root/'Downloads/Cinematic Test - Designed';folder.mkdir(parents=True);file=folder/'LIGHT-Bells_B00M.wav';file.write_bytes(chime())
        sounds=self.audio.catalog();sound=next(s for s in sounds if s['id']!='builtin');self.assertNotIn(str(self.root),str(sounds));self.assertIn('Bells',sound['label']);self.audio.choose(sound['id']);self.assertEqual(self.app.get('alarm_sound'),sound['id'])
        file.unlink();self.assertEqual(self.audio.sound(),self.audio.default);self.assertTrue(self.audio.error)
    def test_invalid_library_id_and_silent_file_leave_setting_unchanged(self):
        with self.assertRaises(ValueError):self.audio.choose('../../secret')
        folder=self.root/'Downloads/Cinematic Test';folder.mkdir(parents=True);file=folder/'Silent.wav'
        with wave.open(str(file),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\0'*48000)
        sound=next(s for s in self.audio.catalog(True) if s['id']!='builtin')
        with self.assertRaises(ValueError):self.audio.choose(sound['id'])
        self.assertIsNone(self.app.get('alarm_sound'))
    def test_24_bit_stereo_is_converted_to_bounded_mono_pcm(self):
        file=self.root/'24bit.wav';samples=(np.sin(np.arange(48000)*.05)*1000000).astype(np.int32);stereo=np.repeat(samples,2).astype(np.uint32);raw=np.stack([(stereo&255),((stereo>>8)&255),((stereo>>16)&255)],axis=1).astype(np.uint8).tobytes()
        with wave.open(str(file),'wb') as w:w.setnchannels(2);w.setsampwidth(3);w.setframerate(48000);w.writeframes(raw)
        content=private_clip(file)
        with wave.open(io.BytesIO(content)) as w:self.assertEqual(w.getnchannels(),1);self.assertEqual(w.getsampwidth(),2);self.assertEqual(w.getframerate(),24000);self.assertLessEqual(w.getnframes()/24000,4)
    def test_source_context_includes_real_dispatch_and_playback_functions(self):
        request='Give alarms cinematic sounds and preserve same-device playback and snooze'
        index=cached_index(ROOT,self.root/'map.json');names=list(index)
        selected=source_bundle(['core.py'],request,names,ROOT);self.assertIn('server.py',selected);self.assertIn('speaker_player.py',selected);self.assertIn('alarm_audio.py',selected)
        sources=context(ROOT,selected,index,request)
        for function in ('def scheduler','def speak','def speech_worker'):self.assertIn(function,sources['server.py'])
        self.assertIn('def play',sources['speaker_player.py']);self.assertIn('def poll',sources['pi/agent.py']);self.assertIn('def modify',sources['feature_timers.py']);self.assertLess(sum(map(len,sources.values())),100000)
    def test_cancellable_speaker_stops_without_saving_alarm_for_continue(self):
        player=SpeakerPlayer();event=threading.Event();event.set()
        with patch.object(player,'start') as start:player.play(chime(),'auto',40,cancel=event);start.assert_not_called()
        player.current={'wav':chime(),'frames':0,'interrupted':False,'cancel':event}
        with patch.object(player,'close'):player.pause()
        self.assertIsNone(player.paused_audio)
    def test_running_speaker_is_cancelled_mid_clip(self):
        player=SpeakerPlayer();event=threading.Event();player.process=Mock();player.process.stdin=Mock()
        with patch.object(player,'start'),patch.object(player,'close') as close:
            t=threading.Thread(target=player.play,args=(chime(),'auto',30),kwargs={'cancel':event});t.start();time.sleep(.05);event.set();t.join(1);self.assertFalse(t.is_alive());close.assert_called()
    def test_pi_sound_revision_acknowledgement_reaches_the_real_handler(self):
        tree=ast.parse((ROOT/'server.py').read_text(encoding='utf-8'));cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Handler');method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='do_POST')
        health={};app=Mock();env={'json':json,'time':time,'APP':app,'PI_HEALTH':health};exec(compile(ast.Module(body=[method],type_ignores=[]),'server-handler-fixture','exec'),env)
        body=json.dumps({'alarm_sound_revision':'0123456789abcdef'}).encode();reply=Mock()
        handler=types.SimpleNamespace(authorized=lambda:True,trusted_local=lambda:False,path='/api/relay-health',headers={'Content-Length':str(len(body))},connection=Mock(),rfile=io.BytesIO(body),client_address=('192.168.1.99',1),send=reply)
        env['do_POST'](handler);self.assertEqual(health['alarm_sound_revision'],'0123456789abcdef');reply.assert_called_once_with(200,{'ok':True})


if __name__=='__main__':unittest.main()
