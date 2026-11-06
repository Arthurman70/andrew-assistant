import ast
import base64
from pathlib import Path
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'server.py').read_text(encoding='utf-8'))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='queue_pi_speech')
        self.now=100;self.audio=queue.Queue()
        def slow_voice(text):
            self.now+=90
            return b'generated audio'
        self.voice=Mock(side_effect=slow_voice)
        env={'base64':base64,'time':SimpleNamespace(time=lambda:self.now),'PI_AUDIO':self.audio,'voice_wav':self.voice}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'pi-delivery-fixture','exec'),env)
        self.send=env['queue_pi_speech']

    def test_cold_voice_keeps_full_delivery_window(self):
        self.send('Hello',seconds=30)
        expires,encoded,_,_=self.audio.get_nowait()
        self.assertEqual(expires-self.now,30)
        self.assertEqual(base64.b64decode(encoded),b'generated audio')

    def test_interruption_during_synthesis_discards_stale_reply(self):
        ticket=threading.Event()
        def interrupted(text):ticket.set();return b'generated audio'
        self.voice.side_effect=interrupted
        self.send('Hello',ticket=ticket)
        self.assertTrue(self.audio.empty())

    def test_followup_and_cancellation_travel_with_the_audio(self):
        ticket=threading.Event()
        self.send('What next?',ticket=ticket,followup='single-use-grant')
        _,_,queued_ticket,grant=self.audio.get_nowait()
        self.assertIs(ticket,queued_ticket);self.assertEqual(grant,'single-use-grant')


if __name__=='__main__':unittest.main()
