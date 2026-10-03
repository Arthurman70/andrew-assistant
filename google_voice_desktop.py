"""Narrow Google Voice adapter for Andrew's existing Windows UI automation.

Only the official voice.google.com tab is eligible. Communications.confirm
controls the final send/call, with one attempt per reviewed draft.
"""
import ctypes
import re
import subprocess
import time
from urllib.parse import urlsplit


def voice_address(value):
    value=value.strip()
    if '://' not in value:value='https://'+value
    parsed=urlsplit(value)
    if parsed.scheme!='https' or parsed.hostname!='voice.google.com' or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('Select the official Google Voice tab in Chrome.')
    return value


def account_page(address,kind):
    parsed=urlsplit(voice_address(address));match=re.match(r'/u/(\d{1,2})(?:/|$)',parsed.path)
    return 'https://voice.google.com/u/'+(match[1] if match else '0')+'/'+('messages' if kind=='text' else 'calls')


def open_normal_chrome():
    from pc_control import CHROME
    # Reuse Chrome's normal session. No alternate profile, debugging port,
    # cookie import, credentials or session tokens.
    return subprocess.Popen([str(CHROME),'--force-renderer-accessibility','https://voice.google.com/'],close_fds=True)


class ChromeVoice:
    def __init__(self):
        from pc_control import PCController
        self.controller=PCController();self.window=None
    def open(self):open_normal_chrome()
    @staticmethod
    def value(control):
        if control.element_info._element.CurrentIsPassword:raise ValueError('Password controls are not eligible.')
        return str(control.iface_value.CurrentValue)
    def address(self,window=None):
        window=window or self.window
        if window is None:raise ValueError('Google Voice is not selected.')
        # Chrome exposes its omnibox as Edit or ComboBox depending on its
        # accessibility provider. Installed web-app windows have no omnibox.
        bars=[]
        for role in ('Edit','ComboBox'):
            for item in window.descendants(control_type=role):
                if re.search(r'address.*(?:search|bar)|omnibox',item.window_text(),re.I) and not self.inside_document(item):
                    bars.append(item)
        if bars:
            values=set()
            for bar in bars:
                try:values.add(self.value(bar))
                except Exception:continue
            if len(values)==1:return voice_address(values.pop())
            raise ValueError('Chrome address controls are not readable by Andrew.')
        # Legacy accessibility exposes the top document URL even when the
        # native address field has no UI Automation Value pattern.
        values=set()
        for document in window.descendants(control_type='Document'):
            if self.inside_document(document):continue
            try:value=document.legacy_properties().get('Value','')
            except Exception:continue
            if value:values.add(value)
        if len(values)==1:return voice_address(values.pop())
        raise ValueError('Chrome has not exposed its page address to Andrew.')
    @staticmethod
    def inside_document(control):
        parent=control.parent()
        for _ in range(30):
            if parent is None:return False
            kind=parent.element_info.control_type
            if kind=='Document':return True
            if kind=='Window':return False
            parent=parent.parent()
        return True
    def select(self):
        matches=[];problems=[];voice_windows=0
        for entry in self.controller.windows():
            if entry['app']!='chrome.exe' or 'voice' not in entry['title'].lower():continue
            voice_windows+=1
            window=self.controller.desktop.window(handle=entry['window']).wrapper_object()
            try:self.address(window)
            except ValueError as exc:problems.append(str(exc));continue
            except Exception:problems.append('Chrome address controls are not readable by Andrew.');continue
            matches.append(window)
        foreground=ctypes.windll.user32.GetForegroundWindow()
        active=[w for w in matches if w.handle==foreground]
        if len(active)==1:self.window=active[0]
        elif len(matches)==1:self.window=matches[0]
        elif len(matches)>1:raise ValueError('More than one Google Voice window is open. Select the one Andrew should use.')
        elif problems:raise ValueError(problems[0])
        else:raise ValueError('Andrew cannot find a visible Google Voice tab in Chrome. Keep that tab selected and its window open.')
        return self.window
    def guard(self):
        if self.window is None or self.controller.process(self.window)!='chrome.exe':raise ValueError('Chrome is no longer open.')
        self.address()
    def controls(self,roles,pattern):
        self.guard();matches=[]
        # Visible controls stay local; never log message text or send it to AI.
        for role in roles:
            for control in self.window.descendants(control_type=role):
                try:
                    if (control.is_visible() and control.is_enabled() and
                            not control.element_info._element.CurrentIsPassword and
                            re.search(pattern,control.window_text(),re.I)):
                        matches.append(control)
                except Exception:continue
        return matches
    def unique(self,roles,pattern):
        matches=self.controls(roles,pattern)
        if len(matches)!=1:raise ValueError('Google Voice control is missing or ambiguous.')
        return matches[0]
    def click(self,control):
        self.guard()
        if not control.is_visible() or not control.is_enabled():raise ValueError('Google Voice changed; request stopped.')
        self.window.set_focus()
        try:invoke=control.iface_invoke
        except Exception:invoke=None
        if invoke is not None:invoke.Invoke()
        else:control.click_input()
    def fill(self,control,value):
        self.guard()
        if control.element_info._element.CurrentIsPassword:raise ValueError('Password fields are not eligible.')
        # Literal SetValue cannot turn message characters into keyboard
        # shortcuts. Never press Enter in the message box or use the clipboard.
        control.iface_value.SetValue(value)
        if self.value(control)!=value:raise ValueError('Chrome did not accept the complete value.')
    @staticmethod
    def wait(predicate,deadline,seconds=8):
        end=min(deadline,time.monotonic()+seconds)
        while time.monotonic()<end:
            try:
                result=predicate()
                if result:return result
            except Exception:pass
            time.sleep(.2)
        raise ValueError('Google Voice did not show the expected control.')
    def ready(self):
        self.select()
        ready=bool(self.controls(('Button',),r'^Send a message$') or
                    self.controls(('Edit','ComboBox'),r'name or number') or
                    self.controls(('TabItem','Hyperlink'),r'^Messages$'))
        if not ready:raise ValueError('Google Voice is open, but Chrome has not exposed its calling or texting controls to Andrew.')
        return True
    def navigate(self,url):
        self.guard();voice_address(url)
        # Only app-generated URLs can reach keyboard navigation.
        if not re.fullmatch(r'https://voice\.google\.com/u/\d{1,2}/(?:messages|calls)',url):
            raise ValueError('Unsupported Google Voice page.')
        self.window.set_focus();self.window.type_keys('^l',set_foreground=True)
        self.window.type_keys(url,with_spaces=True,set_foreground=True)
        self.window.type_keys('{ENTER}',set_foreground=True)
    def action(self,kind,number,message,deadline):
        self.select()
        if time.monotonic()>=deadline:raise ValueError('Request expired.')
        if kind=='hangup':
            self.click(self.unique(('Button',),r'^(End call|Hang up)$'))
            return 'Requested ending the call in Chrome.'
        if kind not in ('text','call'):raise ValueError('Unsupported Google Voice action.')
        from communications import phone_number
        if phone_number(number)!=number:raise ValueError('Use a normalized full number.')
        if kind=='text' and (not isinstance(message,str) or not 1<=len(message)<=1000):raise ValueError('Invalid message.')
        target=account_page(self.address(),kind)
        self.navigate(target)
        if kind=='text':
            self.click(self.wait(lambda:self.unique(('Button',),r'^Send a message$'),deadline))
        recipient=self.wait(lambda:self.unique(('Edit','ComboBox'),r'name or number'),deadline)
        self.fill(recipient,number)
        if kind=='text':
            # Only the number exists here. No message has been filled yet.
            self.guard();recipient.type_keys('{ENTER}',set_foreground=True)
            field=self.wait(lambda:self.unique(('Edit',),r'type a message|enter a message|^message$'),deadline)
            self.fill(field,message)
            if time.monotonic()>=deadline:raise ValueError('Preview expired before sending.')
            self.click(self.unique(('Button',),r'^Send(?: message)?$'))
            self.wait(lambda:not self.value(field).strip() and self.controls(('Text',),r'^'+re.escape(message)+r'$'),deadline,10)
            return 'Google Voice shows the text as sent in Chrome.'
        if time.monotonic()>=deadline:raise ValueError('Preview expired before calling.')
        self.click(self.unique(('Button',),r'^Call(?: '+re.escape(number)+r')?$'))
        self.wait(lambda:self.unique(('Button',),r'^(End call|Hang up)$'),deadline,10)
        return 'Google Voice has started the call in Chrome, using this PC.'
