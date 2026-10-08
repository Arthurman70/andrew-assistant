import datetime as dt
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
from core import Andrew,ROOT
from scheduled_tasks import deadline,extract
from pc_agent import PCAgent
from claude_provider import MODELS,model_id


class TaskTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'data/tests').mkdir(parents=True,exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests');self.app=Andrew(self.temp.name);self.tasks=self.app.tasks
    def tearDown(self):
        if self.tasks.thread:self.tasks.thread.join(3)
        if self.app.pc_agent and self.app.pc_agent.thread:self.app.pc_agent.thread.join(3)
        self.app.db.close();self.temp.cleanup()
    def row(self,n=1):return next(r for r in self.tasks.snapshot() if r['number']==n)
    def fire(self,n=1):
        self.tasks.tick(self.row(n)['due']+.01)
        if self.tasks.thread:self.tasks.thread.join(3);self.assertFalse(self.tasks.thread.is_alive())
    def test_command_at_clock_time_becomes_task_not_timer_or_immediate_launch(self):
        with patch.object(self.app,'pc_agent') as agent:
            answer=self.app.command('open Chrome tomorrow at 7 pm')
            agent.start.assert_not_called()
        self.assertIn('Task 1 scheduled',answer);self.assertEqual(self.row()['command'],'open Chrome');self.assertEqual(self.app.schedule.rows(),[])
        self.assertEqual(dt.datetime.fromtimestamp(self.row()['due']).hour,19)
    def test_relative_task_executes_once_at_due_time(self):
        self.app.command('what time is it')
        self.tasks.create('what time is it','in ten seconds','Clock check');due=self.row()['due']
        with patch.object(self.app,'command',return_value='It is seven.') as command:
            self.tasks.tick(due-1);command.assert_not_called();self.fire();self.tasks.tick(due+5)
        command.assert_called_once_with('what time is it','pc');self.assertEqual(self.row()['status'],'complete');self.assertEqual(self.row()['result'],'It is seven.')
    def test_local_task_changes_app_instead_of_announcing_countdown_finished(self):
        self.tasks.create('add apples to my grocery list','in one second');self.fire()
        self.assertIn('apples',self.app.daily.lists()['grocery']);self.assertEqual(self.row()['status'],'complete');self.assertNotIn('timer',self.row()['result'].lower())
    def test_relative_words_and_prefix_or_suffix_requests(self):
        for phrase in ('open Chrome in ten minutes','in ten minutes open Chrome','in ten minutes, open Chrome','schedule open Chrome in ten minutes','schedule a task in ten minutes to open Chrome'):
            self.assertEqual(extract(phrase),('open Chrome','in ten minutes'))
        self.assertEqual(extract('schedule a task to open Chrome at 7 pm'),('open Chrome','at 7 pm'))
    def test_natural_durations_do_not_launch_early(self):
        for period,seconds in [('in an hour',3600),('in half an hour',1800),('in a quarter of an hour',900),('in one minute and ten seconds',70)]:
            self.assertEqual(deadline(period,1000)[0],1000+seconds);self.assertEqual(extract('open Chrome '+period),('open Chrome',period))
        for phrase in ('open Chrome in a few minutes','open Chrome in -5 minutes'):
            with self.assertRaises(ValueError):extract(phrase)
    def test_timer_alarm_and_reminder_are_still_alerts(self):
        self.app.command('set a tea timer for five minutes');self.app.command('set an alarm for 7 pm');self.app.command('remind me to open Chrome in ten minutes')
        self.assertEqual(self.tasks.snapshot(),[]);self.assertEqual(len(self.app.schedule.rows()),3)
    def test_forecasts_reading_and_app_integration_not_hijacked(self):
        for text in ('weather in Boston for the next three days','weather in Boston tomorrow at 7 am','forecast at 7 pm','open the project in Bambu Studio','use Claude Haiku five point five','tell Claude to look at this page','read this: open Chrome at 7 pm'):
            self.assertIsNone(extract(text),text)
    def test_bad_or_past_time_does_not_run_task_now(self):
        for phrase in ('open Chrome in zero minutes','open Chrome at 25 pm','schedule open Chrome someday'):
            with self.assertRaises(ValueError):self.app.command(phrase)
        self.assertEqual(self.tasks.snapshot(),[])
    def test_calendar_time_named_weekday_and_repeat(self):
        now=dt.datetime(2026,10,8,12).timestamp()
        due,days,clock=deadline('7 am every Monday and Friday',now)
        self.assertEqual(dt.datetime.fromtimestamp(due),dt.datetime(2026,10,9,7));self.assertEqual(days,[0,4]);self.assertEqual(clock,'07:00')
        due,_,_=deadline('next Friday at 7 pm',now);self.assertEqual(dt.datetime.fromtimestamp(due),dt.datetime(2026,10,9,19))
        due,_,_=deadline('2026-10-10T19:30',now);self.assertEqual(dt.datetime.fromtimestamp(due),dt.datetime(2026,10,10,19,30))
    def test_repetition_can_come_before_the_clock(self):
        for text in ('read the news every weekday at 7 am','every weekday at 7 am read the news'):
            command,when=extract(text);self.assertEqual(command,'read the news');self.assertEqual(deadline(when)[1],[0,1,2,3,4])
    def test_multiple_tasks_are_run_in_order_without_duplicate_claims(self):
        for i in range(3):self.tasks.create('what time is it','in one second','Check '+str(i))
        with patch.object(self.app,'command',return_value='ok') as command:
            for _ in range(5):self.tasks.tick(time.time()+10);self.tasks.thread.join(3)
        self.assertEqual(command.call_count,3);self.assertTrue(all(r['status']=='complete' for r in self.tasks.snapshot()))
    def test_busy_pc_queues_then_runs_when_free(self):
        agent=Mock();agent.status.return_value={'state':'running'};self.app.pc_agent=agent;self.tasks.create('what time is it','in one second')
        self.tasks.tick(time.time()+10);self.assertEqual(self.row()['status'],'queued');self.assertIsNone(self.tasks.thread)
        self.app.pc_agent=None;self.fire();self.assertEqual(self.row()['status'],'complete')
    def test_busy_ai_does_not_discard_a_task(self):
        self.tasks.create('what time is it','in one second');self.app.ai_lock.acquire()
        try:self.tasks.tick(time.time()+10);self.assertEqual(self.row()['status'],'queued')
        finally:self.app.ai_lock.release()
        self.fire();self.assertEqual(self.row()['status'],'complete')
    def test_a_task_that_becomes_busy_during_dispatch_is_requeued(self):
        self.tasks.create('what time is it','in one second');self.tasks.notify=Mock()
        with patch.object(self.app,'command',side_effect=['I am still answering the previous AI question. Please try again in a moment.','It is seven.']) as command:
            self.fire();self.assertEqual(self.row()['status'],'queued');self.tasks.notify.assert_not_called();self.fire()
        self.assertEqual(self.row()['status'],'complete');self.assertEqual(command.call_count,2)
    def test_pause_cancel_resume_reschedule_and_manual_run(self):
        self.app.command('open Chrome in five minutes');self.app.command('pause task number one');self.assertEqual(self.row()['status'],'paused')
        self.app.command('resume task 1');self.assertEqual(self.row()['status'],'scheduled')
        original=self.row()['due'];self.app.command('change task 1 to in ten minutes');self.assertGreater(self.row()['due'],original)
        self.app.command('cancel task 1');self.assertEqual(self.row()['status'],'cancelled')
        with patch.object(self.app,'command',return_value='opened') as command:
            self.tasks.modify('1','run');self.fire();command.assert_called_once()
    def test_failure_saved_and_next_task_can_still_run(self):
        self.tasks.create('what time is it','in one second');self.tasks.create('what is the date','in two seconds')
        with patch.object(self.app,'command',side_effect=[ValueError('App not connected'),'ok']):self.fire(1);self.fire(2)
        self.assertEqual(self.row()['status'],'failed');self.assertIn('App not connected',self.row()['result']);self.assertEqual(self.row(2)['status'],'complete')
    def test_restart_retains_due_tasks_but_never_replays_uncertain_running_work(self):
        self.tasks.create('what time is it','in one second');self.tasks.create('what time is it','in two seconds')
        with self.app.lock,self.app.db:self.app.db.execute("UPDATE scheduled_tasks SET status='running' WHERE number=2")
        other=Andrew(self.temp.name)
        try:
            self.assertEqual(other.tasks.snapshot()[1]['status'],'needs_attention')
            other.tasks.tick(time.time()+10);other.tasks.thread.join(3)
            self.assertEqual(next(r for r in other.tasks.snapshot() if r['number']==1)['status'],'complete')
            self.assertEqual(next(r for r in other.tasks.snapshot() if r['number']==2)['status'],'needs_attention')
        finally:other.db.close()
    def test_owner_and_model_stay_with_task_without_changing_active_user(self):
        self.app.command('my name is Sam');sam=self.app.memory.current('pc');self.app.set('provider','claude');self.app.set('claude_model','claude-haiku-5-5');self.tasks.create('what time is it','in one second')
        self.app.command('my name is Kim');kim=self.app.memory.current('pc');self.app.set('provider','grok');observed=[]
        def command(text,source):observed.append((self.app.memory.current(source),self.app.request.scheduled_provider,self.app.request.scheduled_model));return 'ok'
        with patch.object(self.app,'command',side_effect=command):self.fire()
        self.assertEqual(observed,[(sam,'claude','claude-haiku-5-5')]);self.assertEqual(self.app.memory.current('pc'),kim);self.assertEqual(self.app.get('provider'),'grok')
    def test_new_ai_command_contract_schedules_tasks_not_timers(self):
        answer=self.app.actions.consume(json.dumps({'reply':'','commands':[{'name':'task.schedule','args':{'command':'open Chrome','when':'in ten minutes','name':'Browser'}}]}),'browser')
        self.assertIn('scheduled',answer);self.assertEqual(self.row()['source'],'browser');self.assertEqual(self.app.schedule.rows(),[])
    def test_weekday_task_repeats_without_replaying_missed_days(self):
        self.tasks.create('what time is it','7 am every weekday');before=self.row()['due']
        with patch.object(self.app,'command',return_value='ok'):self.fire()
        self.assertEqual(self.row()['status'],'scheduled');self.assertGreater(self.row()['due'],before);self.assertEqual(self.row()['result'],'ok')
    def test_pc_task_waits_for_actual_agent_completion(self):
        release=threading.Event()
        decisions=iter([{'action':'local','args':{'command':'what time is it'}},{'action':'finish','args':{},'answer':'Task finished','status':'complete'}])
        def planner(*args):release.wait(2);return json.dumps(next(decisions))
        agent=PCAgent(self.app,planner=planner,controller_factory=lambda:Mock());self.app.pc_agent=agent
        self.tasks.create('build a test app','in one second');self.tasks.tick(time.time()+10)
        until=time.monotonic()+2
        while not self.row()['pc_task_id'] and time.monotonic()<until:time.sleep(.01)
        self.assertEqual(self.row()['status'],'running');release.set();self.tasks.thread.join(3)
        self.assertEqual(self.row()['status'],'complete');self.assertEqual(self.row()['result'],'Task finished')
    def test_failed_notification_does_not_repeat_execution(self):
        self.tasks.create('what time is it','in one second');self.tasks.notify=Mock(side_effect=OSError('speaker offline'))
        with patch.object(self.app,'command',return_value='ok') as command:self.fire();self.tasks.tick(time.time()+10)
        command.assert_called_once();self.assertEqual(self.row()['status'],'complete')


class HaikuTests(unittest.TestCase):
    def test_spoken_and_pinned_version_resolve_without_changing_version(self):
        for text in ('Haiku 5.5','Haiku five point five','Haiku five five','Claude Haiku 5.5','claude-haiku-5-5'):
            self.assertEqual(model_id(text),'claude-haiku-5-5')
        self.assertEqual(model_id('haiku'),'haiku');self.assertEqual(model_id('Haiku 5.6'),'claude-haiku-5-6')
    def test_catalog_and_voice_selection(self):
        temp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests');app=Andrew(temp.name)
        try:
            catalog=[{'id':'claude','ready':True,'label':'Claude','models':MODELS}]
            with patch('providers.catalog',return_value=catalog):
                for phrase in ('use Haiku 5.5','use Haiku5.5','switch to Claude Haiku five point five','change model to haiku five five'):
                    answer=app.command(phrase);self.assertIn('claude-haiku-5-5',answer);self.assertEqual(app.get('claude_model'),'claude-haiku-5-5');self.assertEqual(app.get('provider'),'claude')
        finally:app.db.close();temp.cleanup()


if __name__=='__main__':unittest.main()
