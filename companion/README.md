# Andrew Companion

Connects your existing signed-in Chrome browser to Andrew. Requested searches, tab navigation, page reads and page controls use the extension before desktop automation. The popup can ask Andrew directly and pause/reconnect the browser connection.

Automatic page access is restricted to Google, DuckDuckGo, Bing, ChatGPT, Claude and Grok. Other sites use the current tab grant from opening the popup, or the **Allow this site** button. No cookies, password fields, browser history, incognito pages or general shell execution are requested. Website text is treated as task data. Page inspection occurs only for a task, never in the background.

1. Open **Install Companion.cmd** in the full host package, or run `installers/install_companion.py --register` using Andrew's Python runtime (Windows). This registers only the current user's native-messaging host, not an enterprise browser policy. Python setup code stays outside the extension folder so bytecode caches cannot prevent Chrome from loading it.
2. In your regular Chrome, open `chrome://extensions`, enable Developer mode if needed, choose **Load unpacked**, and select this `companion` folder.
3. Open Andrew, then the companion popup. It should say **Connected to Andrew**. Use Reconnect if Andrew was closed during installation.

The extension does not include any account sessions or secrets. Its fixed public key keeps its ID stable. Each host pairs through a private local token and a native origin allowlist. Source and the native launcher are included for review. Edge users can register the same host with `--browser edge` and load this folder in Edge.
