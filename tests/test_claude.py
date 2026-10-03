import json
import subprocess
import unittest
from unittest.mock import patch
import claude_provider as claude


class ClaudeTests(unittest.TestCase):
    def run_chat(self,result):
        with patch.object(claude,'status',return_value={'ready':True}), \
             patch.object(claude,'executable',return_value='claude.exe'), \
             patch.object(claude,'record'),patch.object(claude.time,'sleep'), \
             patch.object(claude.subprocess,'run',side_effect=result) as run:
            answer=claude.chat('system','request','sonnet')
            return answer,run

    def test_subscription_answer_model_and_tool_free_stdin(self):
        answer,run=self.run_chat([subprocess.CompletedProcess([],0,json.dumps({'result':'Works','is_error':False}), '')])
        self.assertEqual(answer,'Works')
        args=run.call_args.args[0]
        self.assertEqual(args[args.index('--model')+1],'sonnet')
        self.assertEqual(args[args.index('--tools')+1],'')
        self.assertEqual(run.call_args.kwargs['input'],'request')
        self.assertIn('--no-session-persistence',args)

    def test_service_error_retries_but_quota_does_not(self):
        good=subprocess.CompletedProcess([],0,'{"result":"yes"}','')
        _,run=self.run_chat([subprocess.CompletedProcess([],1,'','503 overloaded'),good])
        self.assertEqual(run.call_count,2)
        with self.assertRaisesRegex(claude.ClaudeError,'usage limit'):
            self.run_chat([subprocess.CompletedProcess([],0,'{"is_error":true,"result":"429 usage limit"}','')])

    def test_api_credentials_are_removed(self):
        with patch.dict(claude.os.environ,{'ANTHROPIC_API_KEY':'secret','ANTHROPIC_BASE_URL':'https://example.org',
                                        'CLAUDE_CODE_USE_BEDROCK':'1','CLAUDE_CODE_OAUTH_TOKEN':'secret'}):
            env=claude.environment()
        for key in ('ANTHROPIC_API_KEY','ANTHROPIC_BASE_URL','CLAUDE_CODE_USE_BEDROCK','CLAUDE_CODE_OAUTH_TOKEN'):
            self.assertNotIn(key,env)

    def test_api_login_is_not_mistaken_for_subscription(self):
        result=subprocess.CompletedProcess([],0,'{"loggedIn":true,"authMethod":"api_key"}','')
        with patch.object(claude,'executable',return_value='claude.exe'),patch.object(claude.subprocess,'run',return_value=result):
            self.assertFalse(claude.status()['ready'])

    def test_error_categories_do_not_mislabel_every_failure_as_login(self):
        self.assertEqual(claude.failure_kind('401 token expired')[0],'auth')
        self.assertEqual(claude.failure_kind('invalid model')[0],'model')
        self.assertEqual(claude.failure_kind('network timeout')[0],'temporary')
