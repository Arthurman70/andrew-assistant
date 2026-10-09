import datetime as dt
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import Andrew


class AlarmFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.andrew = Andrew(self.tmp.name)

    def tearDown(self):
        self.andrew.db.close()
        self.tmp.cleanup()

    def alarms(self, statuses=('active', 'ringing')):
        rows = self.andrew.db.execute("SELECT * FROM timers WHERE kind='alarm'").fetchall()
        return [dict(r) for r in rows if r['status'] in statuses]

    def test_set_alarm_with_dotted_meridiem(self):
        answer = self.andrew.command('set an alarm for 7:30 a.m.')
        self.assertIn('Alarm set for', answer)
        rows = self.alarms()
        self.assertEqual(len(rows), 1)
        due = dt.datetime.fromtimestamp(rows[0]['due'])
        self.assertEqual((due.hour, due.minute), (7, 30))
        self.assertGreater(rows[0]['due'], time.time())

    def test_wake_me_up_sets_alarm(self):
        answer = self.andrew.command('wake me up at 6 pm')
        self.assertIn('Alarm set for', answer)
        rows = self.alarms()
        self.assertEqual(len(rows), 1)
        self.assertEqual(dt.datetime.fromtimestamp(rows[0]['due']).hour, 18)

    def test_ringing_alarm_announces_and_stops(self):
        self.andrew.command('set an alarm for 7 am', source='pi')
        messages = self.andrew.due(time.time() + 2 * 86400)
        self.assertEqual(messages, [{'text': 'Alarm 1 is ringing.', 'source': 'pi','kind':'alarm','id':self.alarms()[0]['id']}])
        self.assertEqual(self.andrew.command('stop the alarm'), 'Cancelled Alarm 1.')
        self.assertEqual(self.alarms(), [])

    def test_stop_without_ringing_alarm_keeps_active_alarm(self):
        self.andrew.command('set an alarm for 7 am')
        self.assertEqual(self.andrew.command('stop the alarm'), 'No alarm is ringing.')
        self.assertEqual(len(self.alarms(('active',))), 1)

    def test_cancel_single_alarm(self):
        self.andrew.command('set an alarm for 8 am')
        self.assertEqual(self.andrew.command('cancel my alarm'), 'Cancelled Alarm 1.')
        self.assertEqual(self.alarms(), [])

    def test_cancel_requires_choice_when_several(self):
        self.andrew.command('set an alarm called gym for 6 am')
        self.andrew.command('set an alarm called work for 7 am')
        self.assertIn('several alarms', self.andrew.command('cancel my alarm'))
        self.assertEqual(len(self.alarms()), 2)
        self.assertEqual(self.andrew.command('cancel the gym alarm'), 'Cancelled gym (alarm 1).')
        self.assertEqual([r['name'] for r in self.alarms()], ['work'])
        self.assertEqual(self.andrew.command('cancel all alarms'), 'Cancelled 1 alarms.')
        self.assertEqual(self.alarms(), [])

    def test_named_alarm_ring_message(self):
        self.andrew.command('set an alarm named gym for 6 am')
        messages = self.andrew.due(time.time() + 2 * 86400)
        self.assertEqual(messages[0]['text'], 'gym (alarm 1) is ringing.')

    def test_list_alarms(self):
        self.assertEqual(self.andrew.command('list alarms'), 'No alarms are set.')
        self.andrew.command('set an alarm called gym for 6:15 am')
        answer = self.andrew.command('list alarms')
        self.assertIn('gym', answer)
        self.assertIn('06:15 AM', answer)

    def test_invalid_time_rejected(self):
        with self.assertRaises(ValueError):
            self.andrew.command('set an alarm for 25:00')
        self.assertEqual(self.alarms(), [])


if __name__ == '__main__':
    unittest.main()
