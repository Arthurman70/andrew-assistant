import datetime as dt
import json
import unittest
from unittest.mock import patch
from tests import test_feature_timer_overhaul as fixture
from feature_alarm_recurrence import next_occurrence, repeat_clause


class RecurringAlarmTests(unittest.TestCase):
    setUp=fixture.ScheduleTests.setUp
    tearDown=fixture.ScheduleTests.tearDown
    rows=fixture.ScheduleTests.rows
    ask=fixture.ScheduleTests.ask
    def alarm(self):return self.rows('alarm')[0]

    def test_daily_weekdays_weekends_and_specific_days_are_local(self):
        for phrase,days in [('every day',list(range(7))),('every weekday',list(range(5))),
                            ('every weekend',[5,6]),('every Monday and Friday',[0,4])]:
            with self.subTest(phrase=phrase):
                self.ask('set work alarm at seven thirty am '+phrase)
                row=self.alarm()
                self.assertEqual(json.loads(row['repeat_days']),days)
                self.assertEqual(row['alarm_time'],'07:30')
                self.assertIn(dt.datetime.fromtimestamp(row['due']).weekday(),days)
                self.ask('cancel all alarms')

    def test_edit_repeat_days_preserves_identity_device_and_clock(self):
        self.ask('set work alarm at 7 am every weekday','pi');before=self.alarm()
        self.ask('change work alarm to 8 am every Monday and Friday')
        row=self.alarm();self.assertEqual(row['id'],before['id']);self.assertEqual(row['source'],'pi')
        self.assertEqual(row['alarm_time'],'08:00');self.assertEqual(json.loads(row['repeat_days']),[0,4])
        self.ask('change work alarm to 9 am');self.assertEqual(json.loads(self.alarm()['repeat_days']),[0,4])
        self.ask('repeat work alarm every day');self.assertEqual(json.loads(self.alarm()['repeat_days']),list(range(7)))
        self.ask('change work alarm to 10 am once');self.assertFalse(self.alarm()['repeat_days'])

    def test_tick_rings_once_dismiss_keeps_schedule_and_cancel_deletes_series(self):
        self.ask('set work alarm at 7 am every weekday');row=self.alarm();now=row['due']
        self.assertEqual(len(self.app.due(now)),1);self.assertEqual(self.app.due(now+1),[])
        future=self.alarm()['next_due'];self.assertGreater(future,now)
        with patch('feature_timers.time.time',return_value=now+2):
            self.assertIn('Next:',self.ask('stop work alarm'))
        self.assertEqual(self.alarm()['due'],future);self.assertEqual(self.alarm()['status'],'active')
        self.assertEqual(len(self.app.due(future)),1)
        self.ask('cancel work alarm');self.assertEqual(self.rows('alarm'),[])
        self.assertEqual(self.app.due(future+86400*8),[])

    def test_snooze_keeps_regular_time_then_dismiss_restores_it(self):
        self.ask('set alarm at 7 am every day');now=self.alarm()['due'];self.app.due(now)
        future=self.alarm()['next_due']
        with patch('feature_timers.time.time',return_value=now+5):self.ask('snooze alarm number 1 for ten minutes')
        row=self.alarm();self.assertEqual(row['next_due'],future);self.assertEqual(row['alarm_time'],'07:00')
        self.assertEqual(len(self.app.due(now+605)),1)
        with patch('feature_timers.time.time',return_value=now+606):self.ask('dismiss alarm number 1')
        self.assertEqual(self.alarm()['due'],future)

    def test_ringing_alarm_does_not_block_next_day_or_replay_missed_days(self):
        self.ask('set alarm at 7 am every day');now=self.alarm()['due'];self.app.due(now)
        future=self.alarm()['next_due'];self.assertEqual(len(self.app.due(future)),1)
        self.assertEqual(self.app.due(future+1),[])
        self.assertEqual(len(self.app.due(future+86400*20)),1)
        self.assertEqual(self.app.due(future+86400*20+1),[])

    def test_long_snooze_does_not_hide_next_regular_occurrence(self):
        self.ask('set alarm at 7 am every day');now=self.alarm()['due'];self.app.due(now)
        future=self.alarm()['next_due']
        with patch('feature_timers.time.time',return_value=now+5):self.ask('snooze alarm number 1 for forty eight hours')
        self.assertEqual(len(self.app.due(future)),1)

    def test_restart_preserves_recurring_calendar(self):
        from core import Andrew
        self.ask('set work alarm at 7 am every weekday');before=self.alarm()
        reopened=Andrew(self.temp.name)
        try:self.assertEqual(reopened.schedule.rows('alarm')[0],before)
        finally:reopened.db.close()

    def test_calendar_boundary_and_invalid_repeat_days(self):
        friday=dt.datetime(2026,10,2,8).timestamp()
        self.assertEqual(dt.datetime.fromtimestamp(next_occurrence('07:30',list(range(5)),friday)),dt.datetime(2026,10,5,7,30))
        with self.assertRaises(ValueError):repeat_clause('7 am every blursday')
        before=self.rows('alarm')
        with self.assertRaises(ValueError):self.ask('set alarm at 7 am every blursday')
        self.assertEqual(self.rows('alarm'),before)

    def test_models_use_the_same_recurring_command(self):
        self.app.actions.execute('schedule.command',{'command':'set work alarm at 7 am every weekday'},'pi')
        self.assertEqual(self.alarm()['source'],'pi');self.assertEqual(json.loads(self.alarm()['repeat_days']),list(range(5)))
        self.assertEqual(self.app.status()['timers'][0]['repeat_label'],'Weekdays')

    def test_unnamed_alarm_repeat_commands_use_its_stable_number(self):
        self.ask('set alarm at 7 am')
        self.ask('repeat alarm number 1 every day')
        self.assertEqual(json.loads(self.alarm()['repeat_days']),list(range(7)))
        self.ask('make alarm number 1 repeat every weekday')
        self.assertEqual(json.loads(self.alarm()['repeat_days']),list(range(5)))
        self.ask('stop repeating alarm number 1');self.assertFalse(self.alarm()['repeat_days'])

    def test_stop_all_only_dismisses_ringing_occurrences(self):
        self.ask('set work alarm at 7 am every day');now=self.alarm()['due'];self.app.due(now)
        self.ask('set future alarm at 8 am every weekday')
        future=next(r for r in self.rows('alarm') if r['name']=='future')
        with patch('feature_timers.time.time',return_value=now+1):self.ask('stop all alarms')
        rows=self.rows('alarm');self.assertEqual(len(rows),2)
        self.assertTrue(all(r['status']=='active' for r in rows))
        self.assertEqual(next(r for r in rows if r['name']=='future')['due'],future['due'])
        self.ask('cancel all alarms');self.assertEqual(self.rows('alarm'),[])
