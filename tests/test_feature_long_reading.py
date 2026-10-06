import io
import json
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import Mock,patch
from core import Andrew,ROOT
from pc_control import PCController
from pc_agent import PCAgent,decision,SYSTEM
from speech_engine import SpeechEngine
from reading import speech_chunks


def wav():
    out=io.BytesIO()
    with wave.open(out,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\x10\x01'*2000)
    return out.getvalue()

class LongReadingTests(unittest.TestCase):
    def control(self,text):
        c=Mock();c.window_text.return_value='Document title';c.is_visible.return_value=True;c.element_info._element.CurrentIsPassword=False
        c.iface_text.DocumentRange.GetText.side_effect=lambda count:text[:count]
        reader=PCController.__new__(PCController);reader.refs={'fresh':(c,'Document title','Document',False)}
        return reader,c

    def test_document_chunks_preserve_tail_and_do_not_skip_text(self):
        text='First section. '*1600+'IMPORTANT ENDING';reader,_=self.control(text);parts=[];offset=0
        while True:
            result=reader.read_text('fresh',offset);parts.append(result['text']);offset=result['next_offset']
            if not result['has_more']:break
        self.assertEqual(''.join(parts),text);self.assertTrue(result['complete']);self.assertIn('IMPORTANT ENDING',parts[-1])

    def test_changed_or_password_text_cannot_be_read(self):
        reader,c=self.control('long text');c.window_text.return_value='Changed'
        with self.assertRaises(ValueError):reader.read_text('fresh')
        c.window_text.return_value='Document title';c.element_info._element.CurrentIsPassword=True
        with self.assertRaises(ValueError):reader.read_text('fresh')
        with self.assertRaises(ValueError):reader.read_text('fresh',offset=-1)

    def test_full_text_speech_is_chunked_and_complete_wav_contains_every_piece(self):
        text=('This is a longer reading. '*250)+'THE FINAL SENTENCE.'
        engine=SpeechEngine(lambda:'am_michael')
        with patch.object(engine,'_synthesize',return_value=wav()) as synth:
            result=engine.synthesize(text)
        spoken=' '.join(c.args[0] for c in synth.call_args_list)
        self.assertEqual(spoken,text);self.assertGreater(synth.call_count,2)
        self.assertTrue(all(len(c.args[0])<=1200 for c in synth.call_args_list))
        with wave.open(io.BytesIO(result),'rb') as w:self.assertEqual(w.getnframes(),2000*synth.call_count)

    def test_short_speech_stays_one_fast_generation(self):
        engine=SpeechEngine(lambda:'am_michael')
        with patch.object(engine,'_synthesize',return_value=wav()) as synth:engine.synthesize('A short answer.')
        synth.assert_called_once_with('A short answer.','am_michael')

    def test_read_action_is_available_to_all_planners(self):
        self.assertEqual(decision('{"action":"read","args":{"ref":"fresh","offset":12000}}')['action'],'read')
        self.assertIn('text_truncated',SYSTEM);self.assertIn('next_offset',SYSTEM)

    def test_long_task_response_is_not_cut_to_old_limit(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data/tests') as tmp:
            app=Andrew(tmp);answer='A reading. '*500+'END OF READING';actions=iter([{'action':'read','args':{'ref':'fresh'}},{'action':'finish','status':'complete','answer':answer}])
            pc=Mock();pc.perform.return_value={'text':answer,'complete':True,'has_more':False}
            agent=PCAgent(app,planner=lambda *a:json.dumps(next(actions)),controller_factory=lambda:pc)
            try:agent.run('Read this entire document aloud','pc');self.assertEqual(agent.status()['answer'],answer)
            finally:app.db.close()

    def test_detailed_local_answers_get_larger_output_budget(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data/tests') as tmp:
            app=Andrew(tmp);app.set('local_model','tiny')
            try:
                with patch('core.request_json',side_effect=[{'models':[{'name':'tiny'}]},{'message':{'content':'{"reply":"Detailed answer","commands":[]}'}}]) as request:
                    app.ai('Read the entire article in detail','local','tiny')
                self.assertEqual(request.call_args.args[1]['options']['num_predict'],4096)
                self.assertEqual(request.call_args.args[1]['options']['num_ctx'],32768)
            finally:app.db.close()

    def test_pasted_reading_needs_no_model_and_preserves_the_whole_text(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data/tests') as tmp:
            app=Andrew(tmp);text='This is the requested text. '*250+'FINAL WORD.'
            try:
                with patch.object(app,'ai',side_effect=AssertionError('No AI needed')):
                    self.assertEqual(app.command('read this text: '+text),text)
            finally:app.db.close()

    def test_document_title_fallback_is_not_claimed_as_the_full_document(self):
        reader,c=self.control('document body');c.iface_text.DocumentRange.GetText.side_effect=RuntimeError('Unsupported');c.iface_value.CurrentValue=None
        result=reader.read_text('fresh');self.assertFalse(result['complete']);self.assertTrue(result['needs_section_inspection'])

    def test_long_playback_deadline_follows_audio_duration(self):
        from audio_utils import playback_timeout
        content=io.BytesIO()
        with wave.open(content,'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(100);w.writeframes(b'\x10\x01'*30000)
        self.assertEqual(playback_timeout(content.getvalue()),330)
        self.assertEqual(playback_timeout(wav()),120)

    def test_read_text_redaction_does_not_corrupt_saved_task_json(self):
        from task_memory import TaskMemory
        with tempfile.TemporaryDirectory(dir=ROOT/'data/tests') as tmp:
            app=Andrew(tmp);tasks=TaskMemory(app)
            history=[{'tool_result':{'text':'The sample password is exampleValue','tail':'Last paragraph'}}]
            try:
                tasks.save('pc',None,'Read a document',history,[],'claude','opus','paused')
                saved=tasks.load('pc',None);self.assertNotIn('exampleValue',str(saved['history']));self.assertEqual(saved['history'][0]['tool_result']['tail'],'Last paragraph')
            finally:app.db.close()
