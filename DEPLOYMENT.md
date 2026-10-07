# Host Andrew on your own website

Install the Windows host first. The VPS runs only a small Python gateway; account sessions and speech engines stay on the PC. Keep the PC awake and running Andrew. This setup is for one trusted owner, not public multi-user hosting or sharing control of a desktop.

## Create private configuration

In the host folder:

```powershell
.venv\Scripts\python.exe installers\configure_web.py --domain andrew.example.com --email owner@example.com --ssh-target andrew-tunnel@YOUR_VPS
```

The `data/web-setup` folder contains a generated temporary login, restricted tunnel key, PC configuration, and **private** server bundle. Do not put these in a public repository or release. Copy `web-bridge.json` and `web_tunnel_ed25519` into the host's `data` folder. Restrict the key's Windows file permissions to the user running Andrew.

## Linux VPS

1. Point an A record for your chosen hostname to the VPS. Install Python 3 and Caddy through your OS packages.
2. Create a non-root `andrew-web` service user. Extract `server-private.tar.gz` into `/opt/andrew-web`. Source files should be root-owned; the `private` folder/config must be readable/writable only by `andrew-web`, since password changes update that file.
3. Run `/usr/bin/python3 /opt/andrew-web/web_portal.py --config /opt/andrew-web/private/config.json` as `andrew-web`, preferably with systemd and restart-on-failure. The gateway binds loopback port 18770 by default.
4. Add a Caddy route for the chosen hostname: `reverse_proxy 127.0.0.1:18770`. Preserve existing routes, validate the configuration, and reload gracefully. With Docker Caddy, choose the private Docker bridge IP using `--server-bind`; never bind the gateway to a public interface. Caddy supplies HTTPS.
5. Create a dedicated `andrew-tunnel` SSH user. Put the generated public key in its `~/.ssh/authorized_keys` with options `restrict,port-forwarding,permitlisten="127.0.0.1:18765",permitopen="127.0.0.1:1",command="/usr/sbin/nologin"`. Keep the SSH account/key permissions correct. The client uses `ssh -N`; this key may establish the designated reverse port but cannot open a shell or arbitrary local forwards.
6. Verify the VPS SSH host fingerprint and pin it in the PC's `data/vps_known_hosts`. Start **installers/Start-WebBridge.ps1**; Start Andrew also runs it when web pairing exists. The gateway-to-PC bridge requires a separate secret token and accepts only approved app routes.

## Use it

Open the HTTPS address, sign in with the generated temporary password, and choose a different password of at least 12 characters. Browser sessions last 90 days and renew once a day during authenticated use. They survive gateway restarts and updates in `private/sessions.sqlite3`; keep this file private along with the configuration. Only hashes of session cookies are stored. Signing out revokes that browser's session; changing the password revokes other devices, including across restarts. Changing the configured account or origin also invalidates old sessions. The app is unavailable while the PC is asleep/offline, but the login page remains available. Talk is explicit push-to-talk; closing/backgrounding the client closes microphone access.

Select Install app / Add to Home Screen for a browser app. The floating widget is an authenticated-window launcher; embed `https://YOUR_HOST/widget.js`. The Android APK's Change server button selects your HTTPS origin. Self-signed certificates and plain HTTP are not accepted by the APK.

Back up `private/config.json` and the PC's private pairing/key files securely. Updating public source must not replace private configuration. Stop `bridge_runner.py` and revoke its authorized key to disconnect PC control. Never forward the unauthenticated loopback port 8765 directly to the internet.
