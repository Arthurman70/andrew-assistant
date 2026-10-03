import time
import unittest
from unittest.mock import Mock,patch
from google_voice_desktop import voice_address,account_page,open_normal_chrome,ChromeVoice

class ChromeVoiceTests(unittest.TestCase):
    def test_opens_normal_chrome_without_a_new_profile_or_debugging(self):
        with patch('google_voice_desktop.subprocess.Popen') as launch:
            open_normal_chrome()
        args=launch.call_args.args[0]
        self.assertEqual(args[-1],'https://voice.google.com/')
        self.assertFalse(any('user-data-dir' in a or 'profile-directory' in a or 'remote-debug' in a for a in args))
    def test_google_account_index_is_preserved(self):
        self.assertEqual(account_page('voice.google.com/u/2/calls','text'),'https://voice.google.com/u/2/messages')
    def test_only_official_secure_voice_pages_are_eligible(self):
        for value in ('http://voice.google.com','https://voice.google.com.evil.test','https://voice.google.com@evil.test',
                      'https://user:password@voice.google.com','https://voice.google.com:8765','http://127.0.0.1'):
            with self.subTest(value=value),self.assertRaises(ValueError):voice_address(value)
        self.assertEqual(voice_address('voice.google.com/u/0/calls'),'https://voice.google.com/u/0/calls')
    def test_message_is_literal_and_password_fields_are_refused(self):
        page=ChromeVoice.__new__(ChromeVoice);page.guard=Mock()
        field=Mock();field.element_info._element.CurrentIsPassword=False
        body='100% ready + {ENTER}\nDo not press any keys.'
        field.iface_value.CurrentValue=body
        page.fill(field,body)
        field.iface_value.SetValue.assert_called_once_with(body)
        field.type_keys.assert_not_called()
        field.element_info._element.CurrentIsPassword=True
        with self.assertRaises(ValueError):page.fill(field,'anything')
    def test_navigation_cannot_interpret_a_user_supplied_key_sequence(self):
        page=ChromeVoice.__new__(ChromeVoice);page.guard=Mock();page.window=Mock()
        with self.assertRaises(ValueError):page.navigate('https://voice.google.com/u/0/calls?x={ENTER}')
        page.window.type_keys.assert_not_called()
    def test_expired_requests_never_reach_any_click(self):
        page=ChromeVoice.__new__(ChromeVoice);page.select=Mock();page.click=Mock()
        with self.assertRaises(ValueError):page.action('text','+12025550100','test',time.monotonic()-1)
        page.click.assert_not_called()
    def test_combobox_omnibox_and_duplicate_wrappers_are_supported(self):
        page=ChromeVoice.__new__(ChromeVoice);page.window=Mock();page.inside_document=Mock(return_value=False)
        field=Mock();field.window_text.return_value='Address and search bar'
        field.element_info._element.CurrentIsPassword=False;field.iface_value.CurrentValue='voice.google.com/u/1/calls'
        page.window.descendants.side_effect=lambda control_type:[field] if control_type in ('Edit','ComboBox') else []
        self.assertEqual(page.address(),'https://voice.google.com/u/1/calls')
    def test_top_document_url_supports_chrome_app_windows(self):
        page=ChromeVoice.__new__(ChromeVoice);page.window=Mock();page.inside_document=Mock(return_value=False)
        document=Mock();document.legacy_properties.return_value={'Value':'https://voice.google.com/u/0/calls'}
        page.window.descendants.side_effect=lambda control_type:[document] if control_type=='Document' else []
        self.assertEqual(page.address(),'https://voice.google.com/u/0/calls')
    def test_iframe_or_fake_web_omnibox_cannot_supply_the_trusted_address(self):
        page=ChromeVoice.__new__(ChromeVoice);page.window=Mock();page.inside_document=Mock(return_value=True)
        field=Mock();field.window_text.return_value='Address and search bar'
        field.legacy_properties.return_value={'Value':'https://voice.google.com/'}
        page.window.descendants.return_value=[field]
        with self.assertRaises(ValueError):page.address()

if __name__=='__main__':unittest.main()
