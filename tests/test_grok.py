import subprocess
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import grok_provider as grok


class GrokTests(unittest.TestCase):
    def test_structured_improvement_uses_schema_and_preserves_tool_restrictions(self):
        schema={'type':'object','properties':{'paths':{'type':'array','items':{'type':'string'}}},'required':['paths']}
        result=subprocess.CompletedProcess([],0,json.dumps({'text':'{"paths":["core.py"]}'}),'')
        with patch.object(grok.subprocess,'run',return_value=result) as run,patch.object(grok,'record'):
            answer=grok.chat('Return JSON','Select files','grok-selected',response_schema=schema)
        self.assertEqual(json.loads(answer),{'paths':['core.py']})
        args=run.call_args.args[0]
        self.assertEqual(json.loads(args[args.index('--json-schema')+1]),schema)
        self.assertEqual(args[args.index('--model')+1],'grok-selected')
        self.assertEqual(args[args.index('--deny')+1],'*')
        self.assertIn('--no-subagents',args)

    def test_service_hiccup_retries_same_account_and_model(self):
        results=[subprocess.CompletedProcess([],1,'','503 service unavailable'),
                 subprocess.CompletedProcess([],0,'{"text":"It works."}','')]
        with patch.object(grok.subprocess,'run',side_effect=results) as run,patch.object(grok,'record'),patch.object(grok.time,'sleep'):
            self.assertEqual(grok.chat('system','test','grok-4.7-build-fast'),'It works.')
            self.assertEqual(run.call_count,2)
            self.assertEqual(run.call_args_list[0].args,run.call_args_list[1].args)

    def test_quota_does_not_claim_signin_or_retry(self):
        result=subprocess.CompletedProcess([],1,'','429 usage limit reached')
        with patch.object(grok.subprocess,'run',return_value=result) as run,patch.object(grok,'record'):
            with self.assertRaisesRegex(grok.GrokError,'usage limit'): grok.chat('system','test')
            run.assert_called_once()

    def test_expired_auth_requires_signin(self):
        result=subprocess.CompletedProcess([],1,'','401 token expired')
        with patch.object(grok.subprocess,'run',return_value=result),patch.object(grok,'record'):
            with self.assertRaisesRegex(grok.GrokError,'fresh account sign-in'): grok.chat('system','test')

    def test_timeout_is_bounded_and_does_not_claim_auth_failure(self):
        with patch.object(grok.subprocess,'run',side_effect=subprocess.TimeoutExpired('grok',45)) as run,patch.object(grok,'record'),patch.object(grok.time,'sleep'):
            with self.assertRaisesRegex(grok.GrokError,'too long'): grok.chat('system','test')
            self.assertEqual(run.call_count,2)

    def test_invalid_response_is_not_remembered_as_an_answer(self):
        result=subprocess.CompletedProcess([],0,'{"text":null}','')
        with patch.object(grok.subprocess,'run',return_value=result),patch.object(grok,'record'):
            with self.assertRaisesRegex(grok.GrokError,'incomplete'): grok.chat('system','test')

    def test_action_failure_recovers_with_same_model_and_tools_disabled(self):
        results=[subprocess.CompletedProcess([],1,'{"text":"I will inspect that."}','max turns reached'),
                 subprocess.CompletedProcess([],0,'{"text":"{\\"action\\":\\"windows\\"}"}','')]
        with patch.object(grok.subprocess,'run',side_effect=results) as run,patch.object(grok,'record') as record:
            answer=grok.chat('Return only JSON.','Original request','grok-4.7')
        self.assertEqual(json.loads(answer),{'action':'windows'})
        self.assertEqual(run.call_count,2)
        for call in run.call_args_list:
            args=call.args[0]
            self.assertEqual(args[args.index('--model')+1],'grok-4.7')
            # A known allowlisted tool is explicitly removed; an empty or
            # unknown allowlist would restore defaults in the real client.
            self.assertEqual(args[args.index('--tools')+1],'read_file')
            self.assertEqual(set(args[args.index('--disallowed-tools')+1].split(',')),
                             {'read_file','search_tool','use_tool'})
            self.assertEqual(args[args.index('--deny')+1],'*')
            self.assertEqual(args[args.index('--permission-mode')+1],'dontAsk')
            self.assertEqual(args[args.index('--max-turns')+1],'1')
            self.assertIn('Return only JSON.',args[args.index('--system-prompt-override')+1])
        retry=run.call_args_list[1].args[0]
        self.assertEqual(retry[retry.index('-p')+1],grok.RECOVERY+'Original request')
        self.assertEqual(record.call_args.args[0],'ready')
        self.assertEqual(record.call_args.args[-1],2)

    def test_repeated_action_failure_is_bounded_without_model_fallback(self):
        result=subprocess.CompletedProcess([],1,'','max turns reached')
        with patch.object(grok.subprocess,'run',return_value=result) as run,patch.object(grok,'record') as record:
            with self.assertRaisesRegex(grok.GrokError,'automatic retry'): grok.chat('system','test')
        self.assertEqual(run.call_count,2)
        for call in run.call_args_list: self.assertNotIn('--model',call.args[0])
        self.assertEqual(record.call_args.args[0],'format')

    def test_structured_turn_limit_is_recovered_even_with_zero_exit(self):
        for output in ('{"type":"error","message":"max turns reached"}',
                       '{"text":"Starting an action","stopReason":"max_turn_requests"}'):
            with self.subTest(output=output):
                results=[subprocess.CompletedProcess([],0,output,''),
                         subprocess.CompletedProcess([],0,'{"text":"Answer","stopReason":"end_turn"}','')]
                with patch.object(grok.subprocess,'run',side_effect=results),patch.object(grok,'record'):
                    self.assertEqual(grok.chat('system','test'),'Answer')

    def test_partial_cancelled_or_truncated_text_is_not_success(self):
        for reason in ('max_tokens','cancelled'):
            with self.subTest(reason=reason):
                result=subprocess.CompletedProcess([],0,json.dumps({'text':'partial','stopReason':reason}),'')
                with patch.object(grok.subprocess,'run',return_value=result),patch.object(grok,'record'):
                    with self.assertRaisesRegex(grok.GrokError,'incomplete'):grok.chat('system','test')

    def test_quota_with_turn_limit_does_not_trigger_recovery(self):
        result=subprocess.CompletedProcess([],1,'','429 usage limit; max turns reached')
        with patch.object(grok.subprocess,'run',return_value=result) as run,patch.object(grok,'record'):
            with self.assertRaisesRegex(grok.GrokError,'usage limit'):grok.chat('system','test')
            run.assert_called_once()

    def test_image_retry_preserves_original_blocks_and_removes_files(self):
        observed=[];contents=[]
        def run(args,**kwargs):
            path=Path(args[args.index('--prompt-file')+1]);observed.append(path)
            contents.append(json.loads(path.read_text(encoding='utf-8')))
            if len(observed)==1:return subprocess.CompletedProcess([],1,'','max turns reached')
            return subprocess.CompletedProcess([],0,'{"text":"Red."}','')
        (grok.ROOT/'data/tests').mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=grok.ROOT/'data/tests') as directory:
            photo=Path(directory)/'test.jpg';photo.write_bytes(b'synthetic-test-image')
            with patch.object(grok.subprocess,'run',side_effect=run),patch.object(grok,'record'):
                self.assertEqual(grok.chat('Describe attached images.','What color?',images=[photo]),'Red.')
        self.assertEqual(contents[1][1:],contents[0])
        self.assertIn('output format',contents[1][0]['text'])
        self.assertEqual(contents[0][0]['text'],'What color?')
        self.assertTrue(all(not p.exists() for p in observed))

    def test_long_recovery_prompt_is_cleaned_up_even_when_client_fails(self):
        observed=[]
        def run(args,**kwargs):
            path=Path(args[args.index('--prompt-file')+1]);observed.append(path)
            text=path.read_text(encoding='utf-8')
            self.assertEqual(text,('' if len(observed)==1 else grok.RECOVERY)+'x'*20000)
            if len(observed)==1:return subprocess.CompletedProcess([],1,'','max turns reached')
            raise OSError('client failed')
        with patch.object(grok.subprocess,'run',side_effect=run),patch.object(grok,'record'):
            with self.assertRaisesRegex(grok.GrokError,'could not start'):grok.chat('system','x'*20000)
        self.assertEqual(len(observed),2)
        self.assertTrue(all(not p.exists() for p in observed))

    def test_long_task_uses_file_and_removes_it_after_call(self):
        observed=[]
        def run(args,**kwargs):
            from pathlib import Path
            path=Path(args[args.index('--prompt-file')+1])
            self.assertEqual(path.read_text(encoding='utf-8'),'x'*20000)
            observed.append(path)
            return subprocess.CompletedProcess([],0,'{"text":"done"}','')
        with patch.object(grok.subprocess,'run',side_effect=run),patch.object(grok,'record'):
            self.assertEqual(grok.chat('system','x'*20000),'done')
        self.assertFalse(observed[0].exists())
