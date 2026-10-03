# Andrew

An open-source assistant with a Windows host, Raspberry Pi voice/screen satellite, and authenticated browser/mobile client. Use local commands for everyday tasks and signed-in Grok, Claude, or OpenAI accounts for conversation and reviewed improvements.

This is an early release, with timers, alarms, reminders, lists, named memories, optional speaker matching, games, model switching, camera requests, and bounded Windows browser tasks. Home Assistant can connect lights and thermostats. Device availability and account limits apply; full Alexa parity is not promised.

## Windows installation

Download **Andrew-Windows.zip** from [Releases](https://github.com/Arthurman70/andrew-assistant/releases), extract it, and open **Install Andrew.cmd**, or run **Andrew-Setup.exe**. Python 3.12 installs for the current user if needed. Speech models download from original publishers and require several GB; rerun to resume downloads. Open the **Andrew** desktop shortcut for an app window.

Select a connected account in Connections. Install and sign in to the official [Grok Build](https://docs.x.ai/build/overview), [Claude Code](https://code.claude.com/docs/en/setup), or [Codex](https://github.com/openai/codex) client. Optional local inference uses [Ollama](https://ollama.com/) and `ollama pull qwen3:0.6b`. No paid API fallback is enabled. Local timers, games, and basic commands work without an AI connection.

Choose a microphone and speaker in Settings. Say **“Hey Andrew, what time is it?”** or **“Hey Andrew, switch to Grok”**, **“use Claude Sonnet”**, **“what model are you using?”**.

Rename with **“Hey Andrew, I’ll call you Charlie”**, **“call yourself Alex”**, or **“change your name to Alex”**. The reply confirms the new **Hey + name** wake phrase; the name survives restarts. You can also use **Settings → Assistant name → Rename**. “My name is …” identifies the speaker and does not rename the assistant.

Adaptive listening is slightly more tolerant in a quiet room. TV/media and quiet mode retain stronger foreground checks. Self-improvement can adjust `wake_tuning.json` within validated bounds; microphone capture and the requirement for a complete wake phrase remain protected.

## Interruption

During a reply, say **“shut up”**, **“stop talking”**, or **“be quiet”**. Say **“continue”** within two minutes to resume remaining speech. Quiet, Continue, and Stop reply buttons are available. A new request replaces a paused answer; resuming speech does not repeat actions. Detection uses local keywords and a foreground-volume check; room acoustics can still affect it.

Browser/mobile speech uses pause/resume buttons. The microphone opens only after **Talk to Andrew** is tapped and closes after Finish request or 15 seconds. It does not listen continuously for interruption words.

## Raspberry Pi satellite

Use 64-bit Raspberry Pi OS, a Pi 4/5, Ethernet, a microphone, speakers, and optionally a webcam/screen. The host PC handles recognition and speech generation and must stay awake.

On the PC, from the installed folder:

```powershell
.venv\Scripts\python.exe installers\make_pi_package.py --pc-ip YOUR_PC_ETHERNET_IP
```

Copy **data/Andrew-Pi-private-pairing.zip** to the Pi, extract it, and run `bash install-pi.sh`. This installs voice/display without erasing the card. Run **Enable-RelayFirewall.ps1** as Administrator to allow encrypted port 8766 from your local Ethernet subnet. The pairing zip contains a private relay token: never publish it.

For updates, verify the Pi SSH fingerprint, pin its key in `data/pi_known_hosts`, and create `data/satellite-pairing.json` with `address`, `user`, `host_alias`, and `mode: "writable"`. The installer places the host public key on the Pi. Existing recovery satellites use their paired key and runtime cache. Use **Connections → Pi updates → Push updates to Pi**. Queued updates survive a PC restart and install when the paired Pi reconnects; deployment status remains visible.

## Browser, widget, PWA, Android

The authenticated HTTPS gateway connects to the Windows host. It uses the same app pages and accounts. Replies play on the requesting browser with their own volume. Talk records one explicit request for local host recognition. A foreground browser and awake PC are required.

- **PWA:** open your HTTPS instance and choose Install app / Add to Home Screen. Private pages and API responses are never cached.
- **Widget:** add `<script src="https://YOUR_ANDREW_HOST/widget.js"></script>` to a page for a floating launcher. Andrew opens in its own authenticated window; the embedding site never receives credentials.
- **Android:** download **Andrew-Android.apk** from Releases. Change server selects your own HTTPS instance. Microphone access requires Android permission and is foreground-only. There is no Play Store listing. Build source with Java 17, Android SDK, and `android/gradlew assembleRelease`, then sign with your own release key.

See [DEPLOYMENT.md](DEPLOYMENT.md) to host your own instance. Never expose the unauthenticated PC loopback service directly. The gateway uses hashed passwords, forced temporary-password changes, secure cookies, CSRF checks, login throttling, and a secret-authenticated SSH bridge.

## Useful commands

Timers and alarms have separate screen lists and durable numbers. Unnamed items become Timer 1, Timer 2, Alarm 1, etc.; names stay available as shortcuts. Edit an item directly under **Edit time or name**, or use voice:

- “Change tea timer to ten minutes” / “change timer number 2 to five minutes.”
- “Add two minutes to the tea timer” / “subtract one minute from timer number 1.”
- “Pause tea timer” / “resume tea timer” / “restart tea timer.”
- “Rename timer number 1 to pasta.”
- “Change work alarm to 7:30 am” / “change the 6 am alarm to 8 am.”
- “Snooze alarm number 1 for ten minutes” when ringing.
- “Cancel timer number 2” / “cancel all alarms” / “list timers and alarms.”

“Make it ten minutes” uses the most recently referenced item on that device for two minutes, scoped to the identified speaker. When several items match, Andrew lists their names/numbers without changing any. Editing an alarm preserves its identity; editing a paused timer keeps it paused. Countdown durations accept “5 min”, “5m 30s”, MM:SS, or HH:MM:SS. Alarm times follow the host PC clock. Existing schedules retain their deadlines; old items that lack an original duration need a new duration before Restart can be used.

Say **Hey + the current assistant name** before PC/Pi requests:

- Set a tea timer for five minutes / change tea timer to three minutes / cancel tea timer.
- Snooze for thirty minutes / change your name to Alex / open settings.
- Open calculator and set a five minute timer.
- Take a photo / record a ten second video, then explicitly send the capture to an AI.
- My name is Sam / remember I prefer tea / show memory / forget that preference.
- Play chess / move e2 to e4 / play trivia / play tic tac toe.
- Improve yourself by … / review latest improvement / save and install the latest improvement.

Improvements stage changes, run checks, back up, install, and support rollback. Authentication, microphone capture, and the updater are protected. Google Voice calls/texts use real signed-in Chrome; setup and explicit confirmation are required. Generated messages are not sent automatically.

## Privacy and development

Before a PC/Pi wake, only keyword spotting and numerical acoustic checks run. Background conversations are not transcribed or saved. Activated requests are processed in memory; addressed text/preferences may go to the selected AI account. Photos/videos are captured locally only when requested, then sent to AI only on request. Named preferences stay local. Speaker matching is probabilistic and is never authentication.

Python 3.12 on Windows: create `.venv`, install `requirements.txt`, then run `python -m unittest discover -s tests`. Physical microphone/speaker, account, and Pi validation are separate from automated tests. Public source excludes user data, models, keys, account sessions, and private deployment files. GPL-3.0-or-later; see LICENSE, COPYING, THIRD_PARTY.md.
