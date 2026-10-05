import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from core import Andrew,ROOT
from pc_agent import PCAgent,decision,is_pc_task
from task_memory import TaskMemory
from update_merge import merge,install

class MemoryTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests');self.root=Path(self.tmp.name);self.app=Andrew(self.root/'data')
    def tearDown(self):
        until=time.monotonic()+3
        while self.app.memory.conversation.running and time.monotonic()<until:time.sleep(.01)
        self.app.db.close();self.tmp.cleanup()
    def identify(self,name='Sam'):self.app.command('my name is '+name);return self.app.memory.current('pc')
    def wait_memory(self):
        until=time.monotonic()+3
        while self.app.memory.conversation.running and time.monotonic()<until:time.sleep(.01)
        self.assertFalse(self.app.memory.conversation.running)

    def test_named_conversations_persist_across_restart_and_provider_changes(self):
        sam=self.identify();self.app.command('set a tea timer for five minutes')
        self.identify('Kim');self.assertNotIn('tea',str(self.app.memory.conversation.recent(self.app.memory.current('pc'),'pc')))
        other=Andrew(self.root/'data')
        try:
            other.command('my name is Sam');other.set('provider','claude')
            self.assertIn('tea',str(other.memory.conversation.recent(sam,'browser')))
        finally:other.db.close()

    def test_guest_transcripts_are_not_attributed_to_previous_named_person(self):
        sam=self.identify();before=self.app.memory.conversation.stats(sam)['turns'];self.app.command('guest mode');self.app.command('what time is it')
        self.assertEqual(self.app.memory.conversation.stats(sam)['turns'],before)

    def test_ten_thousand_words_compact_quietly_without_losing_originals(self):
        sam=self.identify();memory=self.app.memory.conversation
        with patch.object(self.app,'ai',return_value='Sam prefers tea and is building a music app.') as ai:
            memory.append(sam,'pc','tea '*6000,'app '*4000);self.wait_memory()
        self.assertIn('prefers tea',memory.summary(sam));self.assertEqual(memory.stats(sam)['pending_words'],0)
        self.assertEqual(ai.call_args.kwargs['purpose'],'memory')
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM conversation_turns WHERE person=?',(sam,)).fetchone()[0],2)
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)

    def test_failed_compaction_keeps_originals_and_previous_summary(self):
        sam=self.identify();memory=self.app.memory.conversation
        with self.app.db:self.app.db.execute('INSERT INTO conversation_summaries VALUES(?,?,?,?)',(sam,'Known tea preference',0,time.time()))
        with patch.object(self.app,'ai',side_effect=ValueError('Account offline')):
            memory.append(sam,'pc','word '*10000,'reply');self.wait_memory()
        self.assertEqual(memory.summary(sam),'Known tea preference');self.assertGreater(memory.stats(sam)['pending_words'],10000)

    def test_concurrent_turn_is_not_marked_compacted_by_earlier_batch(self):
        sam=self.identify();memory=self.app.memory.conversation;started=threading.Event();release=threading.Event()
        def summarize(*a,**kw):started.set();release.wait(3);return 'Sam likes tea.'
        with patch.object(self.app,'ai',side_effect=summarize):
            memory.append(sam,'pc','word '*10000,'reply');self.assertTrue(started.wait(2));memory.append(sam,'pc','new goal','new reply');release.set();self.wait_memory()
        self.assertEqual(memory.stats(sam)['pending_words'],4)

    def test_delete_profile_erases_conversations_and_learned_tasks(self):
        sam=self.identify();agent=PCAgent(self.app);self.app.pc_agent=agent
        agent.memory.success(sam,'pc','Open calculator',[],'Opened')
        self.app.command('forget everything about me')
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM conversation_turns').fetchone()[0],0)
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM task_recipes').fetchone()[0],0)

    def test_compact_on_demand_is_a_local_command(self):
        sam=self.identify()
        with patch.object(self.app.memory.conversation,'compact',return_value='Compacting') as compact:
            self.assertEqual(self.app.command('compact my memory'),'Compacting');compact.assert_called_once_with(sam)

    def test_clawed_launch_is_fast_and_prompt_content_is_not_rewritten(self):
        with patch('pc_control.PCController') as controller,patch.object(self.app,'ai',side_effect=AssertionError('No planning model needed')):
            controller.return_value.open.return_value={'opened':True}
            self.assertIn('Claude',self.app.command('open clawed'));controller.return_value.open.assert_called_once_with('claude')
            controller.return_value.open.reset_mock()
            self.assertIn('Claude',self.app.command('use clawed app'));controller.return_value.open.assert_called_once_with('claude')
        with patch.object(self.app,'ai',return_value='answer') as ai:
            self.app.command('ask clawed explain why a cat is clawed')
            self.assertEqual(ai.call_args.args,('explain why a cat is clawed','claude',None))

    def test_app_prompt_routes_to_desktop_task_and_build_intents_are_tasks(self):
        self.app.pc_agent=Mock();self.app.pc_agent.start.return_value='Working'
        self.assertEqual(self.app.command('use clawed app to build a chess game'),'Working')
        self.assertIn('claude app',self.app.pc_agent.start.call_args.args[0])
        self.assertTrue(is_pc_task('build a website that uses voice recognition'))
        self.assertEqual(decision('{"action":"type","args":{"ref":"fresh","text":"hello"}}')['action'],'type')

    def test_task_planning_uses_selected_provider_without_consuming_chat_actions(self):
        self.app.set('provider','claude');self.app.set('claude_model','opus');agent=PCAgent(self.app)
        with patch.object(self.app,'ai',return_value='{"action":"windows"}') as ai:
            agent.propose([{'user_request':'Build an app'}],'opus')
        self.assertEqual(ai.call_args.args[1:3],('claude','opus'));self.assertEqual(ai.call_args.kwargs['purpose'],'planner')

    def test_local_planner_receives_action_json_instead_of_conversation_schema(self):
        self.app.set('local_model','tiny')
        with patch('core.request_json',side_effect=[{'models':[{'name':'tiny'}]},{'message':{'content':'{"action":"windows"}'}}]) as request,patch.object(self.app.actions,'consume',side_effect=AssertionError('Planner is not conversation')):
            self.assertEqual(self.app.ai('plan','local','tiny',purpose='planner',system_override='Return action JSON'),'{"action":"windows"}')
        self.assertEqual(request.call_args.args[1]['format'],'json')

    def test_failed_route_automatically_continues_with_another_tool(self):
        actions=iter([{'action':'open','args':{'app':'claude'}},{'action':'finish','status':'failed','answer':'The desktop route did not appear.'},{'action':'open','args':{'app':'chrome','url':'https://claude.ai'}},{'action':'finish','status':'complete','answer':'Opened Claude in Chrome.'}])
        controller=Mock();controller.perform.side_effect=[ValueError('missing app'),{'window':42}]
        agent=PCAgent(self.app,planner=lambda *a:json.dumps(next(actions)),controller_factory=lambda:controller)
        agent.run('Use Claude to build the app','pc');self.assertEqual(agent.status()['state'],'complete');self.assertEqual(controller.perform.call_count,2)

    def test_success_recipes_are_person_scoped_and_drop_stale_ui_references(self):
        sam=self.identify();tasks=TaskMemory(self.app)
        history=[{'proposed_action':{'action':'click','args':{'ref':'old:1'},'progress':'Open reply'}},{'tool_result':{'ok':True}}, {'proposed_action':{'action':'open','args':{'app':'bad'}}},{'tool_result':{'error':'bad app'}}]
        tasks.success(sam,'pc','Open Claude',history,'Opened')
        self.assertNotIn('old:1',str(tasks.recipes(sam,'pc','Open Claude')));self.assertNotIn('bad',str(tasks.recipes(sam,'pc','Open Claude')))
        kim=self.identify('Kim');self.assertEqual(tasks.recipes(kim,'pc','Open Claude'),[])

    def test_resume_after_restart_does_not_duplicate_completed_timer(self):
        sam=self.identify();command='set a tea timer for five minutes'
        history=[{'proposed_action':{'action':'local','args':{'command':command}}},{'tool_result':{'answer':self.app.command(command)}}]
        tasks=TaskMemory(self.app);tasks.save('pc',sam,'Open calculator and set a tea timer',history,[{'action':'local','ok':True}],'claude','opus','paused')
        actions=iter([{'action':'local','args':{'command':command}},{'action':'finish','status':'complete','answer':'Finished.'}])
        agent=PCAgent(self.app,planner=lambda *a:json.dumps(next(actions)))
        agent.start('continue the PC task','pc');agent.thread.join(3)
        self.assertFalse(agent.thread.is_alive());self.assertEqual(agent.status()['state'],'complete');self.assertEqual(len(self.app.status()['timers']),1)

    def test_continue_question_is_handled_without_bouncing_back_to_user(self):
        actions=iter([{'action':'windows','args':{}},{'action':'finish','status':'needs_input','answer':'Would you like me to continue the build?'},{'action':'inspect','args':{}},{'action':'finish','status':'complete','answer':'The build finished.'}])
        controller=Mock();controller.perform.return_value={'window':42}
        agent=PCAgent(self.app,planner=lambda *a:json.dumps(next(actions)),controller_factory=lambda:controller)
        agent.run('Build the app','pc');self.assertEqual(agent.status()['state'],'complete');self.assertFalse(agent.waiting('pc'))

    def test_generating_app_cannot_be_reported_as_a_finished_build(self):
        actions=iter([{'action':'inspect','args':{}},*[{'action':'finish','status':'complete','answer':'Done'}]*3])
        controller=Mock();controller.perform.return_value={'window':42,'ai_busy':True}
        agent=PCAgent(self.app,planner=lambda *a:json.dumps(next(actions)),controller_factory=lambda:controller)
        agent.run('Build the app','pc');self.assertEqual(agent.status()['state'],'paused');self.assertIn('not finished',agent.status()['answer'])

    def test_explicit_build_provider_survives_task_handoff(self):
        self.app.pc_agent=Mock();self.app.pc_agent.start.return_value='Working'
        self.assertEqual(self.app.command('ask clawed build a website'),'Working')
        self.app.pc_agent.start.assert_called_once_with('build a website','pc',provider='claude',model=None)

    def test_credentials_are_not_saved_in_conversation_archive(self):
        sam=self.identify();self.app.command('remember my password is exampleSecret')
        self.assertNotIn('exampleSecret',str(self.app.memory.conversation.recent(sam,'pc')))

    def test_unavailable_planner_uses_another_account_without_changing_selected_model(self):
        self.app.set('provider','claude');self.app.set('claude_model','opus');agent=PCAgent(self.app)
        with patch.object(self.app,'ai',side_effect=[ValueError('Usage limit'),'{"action":"windows"}']) as ai:
            self.assertIn('windows',agent.propose([{'user_request':'Use Claude app'}],'opus'))
        self.assertEqual(ai.call_args_list[1].args[1],'openai');self.assertEqual(agent.status()['planner_provider'],'openai');self.assertEqual(self.app.get('provider'),'claude')

class UpdatePreservationTests(unittest.TestCase):
    def test_merge_keeps_self_improvement_and_unrelated_upstream_fix(self):
        self.assertEqual(merge(b'A=1\nB=1\n',b'A=2\nB=1\n',b'A=1\nB=3\n'),b'A=2\nB=3\n')
        self.assertEqual(merge(b'A=1\nB=1\n',b'A=1\r\nB=1\r\n',b'A=1\nB=3\n'),b'A=1\nB=3\n')
    def test_conflicting_update_is_saved_without_overwriting_any_live_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,dest=Path(tmp)/'upstream',Path(tmp)/'installed';source.mkdir();dest.mkdir();(dest/'data/update-baseline').mkdir(parents=True)
            for root,text in [(source,'A=3\n'),(dest,'A=2\n'),(dest/'data/update-baseline','A=1\n')]: (root/'core.py').write_text(text)
            (source/'new.py').write_text('NEW=1\n');report=install(source,dest)
            self.assertEqual(report['status'],'needs_merge');self.assertEqual((dest/'core.py').read_text(),'A=2\n');self.assertFalse((dest/'new.py').exists());self.assertTrue(Path(report['candidate']).is_dir())
    def test_official_update_preserves_customization_and_private_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,dest=Path(tmp)/'upstream',Path(tmp)/'installed';source.mkdir();dest.mkdir();(dest/'data/update-baseline').mkdir(parents=True)
            for root,text in [(source,'A=1\nB=3\n'),(dest,'A=2\nB=1\n'),(dest/'data/update-baseline','A=1\nB=1\n')]: (root/'core.py').write_text(text)
            (dest/'data/credential.txt').write_text('PRIVATE');report=install(source,dest)
            self.assertEqual(report['status'],'installed');self.assertEqual((dest/'core.py').read_text(),'A=2\nB=3\n');self.assertEqual((dest/'data/credential.txt').read_text(),'PRIVATE')

    def test_source_checkout_line_endings_do_not_break_package_checksums(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            source,dest=Path(tmp)/'source',Path(tmp)/'installed';source.mkdir()
            (source/'core.py').write_bytes(b'A=1\n')
            (source/'package_manifest.json').write_text(json.dumps({'files':{'core.py':hashlib.sha256(b'A=1\r\n').hexdigest()}}))
            self.assertEqual(install(source,dest)['status'],'installed')
