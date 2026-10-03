import datetime as dt
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from core import Andrew,ROOT
from feature_timers import clock_target


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'data/tests').mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests');self.app=Andrew(self.temp.name)
        self.ai=patch.object(self.app,'ai',side_effect=AssertionError('Schedule command reached AI'));self.ai.start()
    def tearDown(self):
        self.ai.stop();self.app.db.close();self.temp.cleanup()
    def rows(self,kind='timer'):return self.app.schedule.rows(kind)
    def ask(self,text,source='pc'):return self.app.command(text,source)

    def test_unnamed_items_have_distinct_stable_numbers_and_names(self):
        self.ask('set a five minute timer');self.ask('set a ten minute timer')
        self.assertEqual([(r['number'],r['name']) for r in self.rows()],[(1,'Timer 1'),(2,'Timer 2')])
        self.ask('cancel timer number one');self.ask('set a two minute timer')
        self.assertEqual({r['number'] for r in self.rows()},{2,3})
        reopened=Andrew(self.temp.name)
        try:self.assertEqual({r['number'] for r in reopened.schedule.rows('timer')},{2,3})
        finally:reopened.db.close()

    def test_same_named_timer_and_alarm_never_collide(self):
        self.ask('set a tea timer for five minutes');self.ask('set a tea alarm for 7 am')
        alarm=self.rows('alarm')[0]
        self.ask('change tea timer to ten minutes');self.ask('cancel tea timer')
        self.assertEqual(self.rows(),[]);self.assertEqual(self.rows('alarm')[0]['due'],alarm['due'])

    def test_alarm_edit_keeps_identity_and_source_instead_of_creating_new_alarm(self):
        self.ask('set an alarm called work for 6 am','pi');before=self.rows('alarm')[0]
        self.ask('change work alarm to seven thirty am')
        rows=self.rows('alarm');self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['id'],before['id']);self.assertEqual(rows[0]['source'],'pi')
        self.assertEqual(dt.datetime.fromtimestamp(rows[0]['due']).hour,7)
        self.assertEqual(dt.datetime.fromtimestamp(rows[0]['due']).minute,30)

    def test_named_alarm_selection_by_time(self):
        self.ask('set an alarm for 6 am');self.ask('set an alarm for 8 am')
        self.ask('change the 6 am alarm to 7 am')
        self.assertEqual({dt.datetime.fromtimestamp(r['due']).hour for r in self.rows('alarm')},{7,8})

    def test_single_item_edit_and_explicit_number_with_multiple(self):
        self.ask('set a five minute timer');self.ask('change my timer to eight minutes')
        self.assertAlmostEqual(self.rows()[0]['due']-time.time(),480,delta=2)
        self.ask('set a three minute timer');self.ask('change timer number one to nine minutes')
        self.assertEqual(next(r['duration'] for r in self.rows() if r['number']==1),540)

    def test_duplicate_names_have_concrete_choices_and_no_bulk_mutation(self):
        self.ask('set a tea timer for two minutes');self.ask('set a tea timer for five minutes')
        before=[r['due'] for r in self.rows()]
        result=self.ask('change tea timer to ten minutes')
        self.assertIn('timer 1',result);self.assertIn('timer 2',result)
        self.assertEqual(before,[r['due'] for r in self.rows()])
        self.ask('change timer number two to seven minutes')
        self.assertEqual(next(r['duration'] for r in self.rows() if r['number']==2),420)

    def test_duplicate_names_prefer_requesting_device_and_allow_explicit_scope(self):
        self.ask('set a tea timer for two minutes','pc');self.ask('set a tea timer for five minutes','pi')
        self.ask('change tea timer to ten minutes','pc')
        self.assertEqual(next(r['duration'] for r in self.rows() if r['source']=='pi'),300)
        self.ask('change tea timer to seven minutes on the Pi','pc')
        self.assertEqual(next(r['duration'] for r in self.rows() if r['source']=='pi'),420)

    def test_add_subtract_and_pause_resume_preserve_remaining_time(self):
        self.ask('set a tea timer for ten minutes');initial=self.rows()[0]['due']
        self.ask('add two minutes to the tea timer')
        self.assertAlmostEqual(self.rows()[0]['due']-initial,120,delta=1)
        self.ask('subtract one minute from the tea timer');self.ask('pause the tea timer')
        row=self.rows()[0];self.assertEqual(row['status'],'paused')
        self.assertAlmostEqual(row['remaining'],660,delta=2)
        self.assertEqual(self.app.due(time.time()+86400),[])
        self.ask('resume tea timer');self.assertAlmostEqual(self.rows()[0]['due']-time.time(),660,delta=2)

    def test_paused_timer_persists_and_can_be_edited_or_cancelled(self):
        self.ask('set a five minute timer');self.ask('pause timer number one')
        reopened=Andrew(self.temp.name)
        try:self.assertEqual(reopened.schedule.rows('timer')[0]['status'],'paused')
        finally:reopened.db.close()
        self.ask('change timer number one to twenty minutes');self.assertEqual(self.rows()[0]['status'],'paused')
        self.assertEqual(self.rows()[0]['remaining'],1200)
        self.ask('cancel timer number one');self.assertEqual(self.rows(),[])

    def test_restart_uses_original_duration_and_names_can_be_changed(self):
        self.ask('set a tea timer for ten minutes');self.ask('rename tea timer to pasta')
        row=self.rows()[0];self.assertEqual(row['name'],'pasta');self.assertEqual(row['number'],1)
        self.ask('restart pasta timer');self.assertAlmostEqual(self.rows()[0]['due']-time.time(),600,delta=2)

    def test_short_followups_are_scoped_expire_and_do_not_hijack_chat(self):
        self.ask('set a tea timer for ten minutes','pc');self.ask('make it twelve minutes','pc')
        self.assertEqual(self.rows()[0]['duration'],720)
        self.assertIn('Name the timer',self.ask('make it three minutes','pi'))
        self.app.schedule.recent['pc']=(0,self.rows()[0]['id'],None)
        self.assertIn('Name the timer',self.ask('make it three minutes','pc'))

    def test_alarm_snooze_and_stop_only_target_ringing_items(self):
        self.ask('set an alarm for 7 am');self.ask('set an alarm for 8 am')
        first=next(r for r in self.rows('alarm') if r['number']==1)
        self.app.due(first['due']+.1)
        self.ask('snooze alarm number one for five minutes')
        self.assertAlmostEqual(next(r['due'] for r in self.rows('alarm') if r['number']==1)-time.time(),300,delta=2)
        self.assertEqual(len(self.rows('alarm')),2)
        self.assertEqual(self.ask('stop the alarm'),'No alarm is ringing.')
        self.assertEqual(len(self.rows('alarm')),2)

    def test_invalid_edit_and_subtraction_leave_existing_deadline(self):
        self.ask('set a tea timer for five minutes');before=self.rows()[0]['due']
        for phrase in ('change tea timer to negative five minutes','subtract ten minutes from tea timer'):
            with self.subTest(phrase=phrase),self.assertRaises(ValueError):self.ask(phrase)
            self.assertEqual(self.rows()[0]['due'],before)
        self.ask('set a work alarm for 6 am');before=self.rows('alarm')[0]['due']
        with self.assertRaises(ValueError):self.ask('change work alarm to 25:70')
        self.assertEqual(self.rows('alarm')[0]['due'],before)

    def test_lists_distinguish_types_and_reminders(self):
        self.ask('set a tea timer for five minutes');self.ask('set a work alarm for 7 am')
        self.app.timer('take pills',60,'reminder')
        self.assertNotIn('work',self.ask('list timers'));self.assertNotIn('tea',self.ask('list alarms'))
        self.assertNotIn('take pills',self.ask('list timers and alarms'))

    def test_all_cancel_is_explicit_and_does_not_touch_reminders(self):
        self.ask('set a tea timer for five minutes');self.ask('set an alarm for 7 am')
        self.app.timer('take pills',60,'reminder')
        self.ask('cancel all timers and alarms')
        self.assertEqual(self.rows(),[]);self.assertEqual(self.rows('alarm'),[])
        self.assertEqual(len(self.rows('reminder')),1)

    def test_ordered_edit_chain_uses_shared_scheduler(self):
        self.ask('set a tea timer for five minutes then add two minutes to tea timer then pause tea timer')
        self.assertEqual(len(self.rows()),1);self.assertEqual(self.rows()[0]['status'],'paused')
        self.assertAlmostEqual(self.rows()[0]['remaining'],420,delta=2)

    def test_model_actions_share_kind_safe_editing(self):
        self.ask('set a tea timer for five minutes');self.ask('set a tea alarm for 7 am')
        self.app.actions.execute('timer.edit',{'name':'tea','seconds':480},'pc')
        self.app.actions.execute('schedule.command',{'command':'change alarm number 1 to 8 am'},'pc')
        self.assertEqual(self.rows()[0]['duration'],480)
        self.assertEqual(dt.datetime.fromtimestamp(self.rows('alarm')[0]['due']).hour,8)

    def test_legacy_default_and_duplicate_rows_migrate_without_losing_deadlines(self):
        directory=Path(self.temp.name)/'legacy';directory.mkdir()
        db=sqlite3.connect(directory/'andrew.db')
        db.execute('CREATE TABLE timers(id TEXT PRIMARY KEY,name TEXT,due REAL,status TEXT,kind TEXT)')
        deadline=time.time()+600
        for i in (1,2):db.execute('INSERT INTO timers VALUES (?,?,?,?,?)',(str(i),'default',deadline+i,'active','timer'))
        db.commit();db.close();app=Andrew(directory)
        try:
            self.assertEqual({r['name'] for r in app.schedule.rows('timer')},{'Timer 1','Timer 2'})
            self.assertEqual([r['due'] for r in app.schedule.rows('timer')],[deadline+1,deadline+2])
            self.assertIn('predates saved durations',app.command('restart timer number one'))
            self.assertEqual([r['due'] for r in app.schedule.rows('timer')],[deadline+1,deadline+2])
        finally:app.db.close()

    def test_clock_dates_spoken_times_and_invalid_past_date(self):
        now=dt.datetime(2026,10,3,10)
        self.assertEqual(clock_target('tomorrow at seven thirty am',now),dt.datetime(2026,10,4,7,30))
        self.assertEqual(clock_target('7:30 p.m.',now),dt.datetime(2026,10,3,19,30))
        with self.assertRaises(ValueError):clock_target('today at 7 am',now)

    def test_duration_shortcuts_and_named_edits(self):
        self.assertEqual(self.app.seconds('5m 30s'),330)
        self.assertEqual(self.app.seconds('1:30'),90)
        self.assertEqual(self.app.seconds('1:30:00'),5400)
        self.assertEqual(self.app.seconds('one and a half hours'),5400)
        self.ask('set a tea timer for 5 min');self.ask('change tea to 10 minutes')
        self.assertEqual(self.rows()[0]['duration'],600)

    def test_reported_device_followup_extends_only_pc_timer(self):
        self.ask('set a five minute timer','pi');self.ask('set a ten minute timer','pc')
        self.app.schedule.recent.clear()
        before={r['source']:r['due'] for r in self.rows()}
        self.ask('The one on the PC, add 30 minutes to it.')
        after={r['source']:r['due'] for r in self.rows()}
        self.assertAlmostEqual(after['pc']-before['pc'],1800,delta=1)
        self.assertEqual(after['pi'],before['pi'])
