# FileJet – Run and Deploy Guide

This guide takes you from a fresh checkout to a running system, step by step:

| Section | Use it when |
|---|---|
| [1. What you are running](#1-what-you-are-running) | First time – the three parts and their ports |
| [2. Install (once)](#2-install-once) | Before anything else |
| [3. Quick start on one PC](#3-quick-start-on-one-pc-one-command) | Fastest way to try it |
| [4. Run each part one by one](#4-run-each-part-one-by-one) | You want to start/stop each part yourself |
| [5. Two PCs on the same network](#5-test-with-two-pcs-on-the-same-network) | Testing between real computers in the office |
| [6. First use in the app](#6-first-use-in-the-app) | Accounts, users, sharing a folder, sending |
| [7. Build the installable app](#7-build-the-installable-app-filejetexe) | Making `FileJet.exe` for users |
| [8. Deploy the server without Docker](#8-deploy-the-server-without-docker-windows-or-linux) | Production on a Windows or Linux server (recommended); **8.0 = iotgateway.live** |
| [9. Deploy the server with Docker](#9-deploy-the-server-with-docker-optional) | Alternative, if you use Docker |
| [10. Roll out to users](#10-roll-out-to-users) | Giving FileJet to your team |
| [11. Operate: update, back up, logs](#11-operate-update-back-up-logs) | Day-2 tasks |
| [12. Troubleshooting](#12-troubleshooting) | Something does not work |

All commands are run from the project folder `ftp_app` unless a step says otherwise.
Windows commands are for **PowerShell**; Linux/macOS commands are for **bash**.

---

## 1. What you are running

FileJet has three parts. **Files never pass through the servers** – they go directly from one PC to the other.

| Part | What it does | Where it runs | Port(s) |
|---|---|---|---|
| **FileJet app** (`filejet.py` / `FileJet.exe`) | The desktop app: folders, users, sending/receiving, chat | Every user's PC | – |
| **Cloud** (`cloud/`, Django) | Accounts, sign-in, users, folders & permissions, transfer **metadata**, web dashboard, e-mails | Server | 8000 (HTTP) |
| **Signaling server** (`server/`, FastAPI) | Introduces two PCs to each other, online status, encrypted chat/control messages. Never sees file data | Server | 8765 (WebSocket), 8766 (TCP reflector for NAT hole punching) |

The cloud and the signaling server **must share one secret**: `P2P_CLOUD_JWT_SECRET`.
The app only needs to know the **cloud URL**; it asks the cloud where the signaling server is.

---

## 2. Install (once)

Requirements: **Python 3.10+** (tested with 3.13), Git or the project folder, ~1 GB disk.
Optional: **ffmpeg** on the PATH for video thumbnails.

**Windows (PowerShell)**
```powershell
cd C:\Users\PYTHON\Desktop\pythonExamples\ftp_app
py -m venv .venv
.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
```

**Linux / macOS (bash)**
```bash
cd ftp_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Check it worked:
```powershell
python -c "import PySide6, django, fastapi; print('ok')"
```

---

## 3. Quick start on one PC (one command)

```powershell
python run_dev.py --apps
```

This starts the cloud (`http://127.0.0.1:8000/`) and the signaling server (`ws://127.0.0.1:8765/ws`) with a
matching secret, and opens the FileJet app **twice** (profiles `one` and `two`) so you can play both sides.

- Create a **different account in each window** (one account can be online in only one app at a time).
- E-mails (folder invitations) appear on the web at **http://127.0.0.1:8000/ → Inbox** during development.
- Press **Ctrl+C** in the terminal to stop everything.

Without `--apps`, only the two servers start; open the app yourself (see 4.3).

---

## 4. Run each part one by one

Use **three or four terminals**. In each one, first activate the virtual environment
(`.venv\Scripts\Activate.ps1`) and `cd` into the project folder.

Pick a secret once and use the **same value** in terminals 1 and 2:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 4.1 Terminal 1 – Cloud

```powershell
cd cloud
$env:P2P_CLOUD_JWT_SECRET = "PASTE-YOUR-SECRET"
$env:P2P_SIGNALING_URL   = "ws://127.0.0.1:8765/ws"
python manage.py migrate                    # creates/updates the database (db.sqlite3)
python manage.py createsuperuser            # optional: admin login for http://127.0.0.1:8000/admin/
python manage.py runserver 127.0.0.1:8000
```
bash equivalent: `export P2P_CLOUD_JWT_SECRET="..."` instead of `$env:...= "..."`.

Check: open **http://127.0.0.1:8000/** – you should see the FileJet sign-in page.

### 4.2 Terminal 2 – Signaling server

```powershell
$env:P2P_CLOUD_JWT_SECRET = "PASTE-YOUR-SECRET"      # exactly the same as terminal 1
python -m server.main --host 127.0.0.1 --port 8765
```

Check: open **http://127.0.0.1:8765/healthz** → `{"ok":true,...}`.

### 4.3 Terminal 3 – The app (first user)

```powershell
python filejet.py
```

On the sign-in screen the bottom line shows **Cloud: http://127.0.0.1:8000/** – click **Change** if your
cloud runs elsewhere. Create an account or sign in.

### 4.4 Terminal 4 – The app again (second user, same PC)

```powershell
python filejet.py --profile two
```

`--profile` keeps a separate local data folder so two accounts can run on one PC.

**Stopping:** press **Ctrl+C** in terminals 1 and 2; close the app windows.

**Start order:** cloud → signaling server → apps. (If the app shows a red “offline” bar, the signaling server
is not running or its secret differs – see [Troubleshooting](#12-troubleshooting).)

---

## 5. Test with two PCs on the same network

PC **A** runs the servers (and may also run the app); PC **B** only runs the app.

1. Find PC A’s IP address: `ipconfig` (e.g. `192.168.1.20`).
2. On PC A, terminal 1 (cloud) – listen on the network and allow the IP:
   ```powershell
   cd cloud
   $env:P2P_CLOUD_JWT_SECRET  = "PASTE-YOUR-SECRET"
   $env:P2P_SIGNALING_URL    = "ws://192.168.1.20:8765/ws"
   $env:DJANGO_ALLOWED_HOSTS = "192.168.1.20,localhost,127.0.0.1"
   $env:CLOUD_PUBLIC_URL     = "http://192.168.1.20:8000/"
   python manage.py migrate
   python manage.py runserver 0.0.0.0:8000
   ```
3. On PC A, terminal 2 (signaling):
   ```powershell
   $env:P2P_CLOUD_JWT_SECRET = "PASTE-YOUR-SECRET"
   python -m server.main --host 0.0.0.0 --port 8765
   ```
4. **Windows Firewall on PC A:** allow inbound TCP **8000, 8765, 8766** (or click *Allow* when Windows asks).
5. On both PCs start the app (`python filejet.py` or `FileJet.exe`), click **Change** on the sign-in
   screen and enter `http://192.168.1.20:8000/`.
6. The first time a transfer starts, Windows may ask to allow FileJet through the firewall – click **Allow**
   (private networks). This lets the two PCs connect directly.

---

## 6. First use in the app

1. **Create accounts** – one per person. Each account has a 9-digit **ID** (shown at the bottom of
   *Shared with me* and on the web under *Users*).
2. **Add a user** – *Users* → enter their ID (or username/e-mail) → **Add user**. You now see each other
   online/offline and can **Chat**.
3. **Share a folder** – *My Folders* → **+** → create a new folder or pick an existing one (PC or NAS)
   → **Give access…** → choose the user (or a group), a role (*Uploader, Viewer, Editor, Manager* or custom
   View/Upload/Edit/Delete) and optionally an expiry date. The user gets an e-mail with the folder ID.
4. **The user opens it** – *Shared with me* → click the folder. Buttons they are not allowed to use are greyed.
5. **Send** – *Send files* / *Send folder*. Progress, sizes and speed show in *My uploads* and *Transfers*;
   the owner sees *Arriving now* and *Received files*. If the owner is offline, files wait on the sender’s
   PC and are delivered automatically when the owner is online. Interrupted transfers resume from the last chunk.
6. **Web dashboard** – `http://<cloud>/` shows transfers, folders, users and statistics (metadata only).

Layout tips: drag the lines between panels to resize, double-click a line to collapse/restore,
**Ctrl+B** side bar, **Ctrl+J** bottom panel, *Settings → Reset layout*.

---

## 7. Build the installable app (`FileJet.exe`)

Build on each operating system you want to support (Windows builds `.exe`, macOS/Linux build their own binary).

```powershell
pip install pyinstaller
python build_desktop.py
```

Result: **`dist\FileJet.exe`** (~55 MB, single file, no Python needed on users’ PCs).
The servers are **not** inside the exe.

Optional: preset the cloud URL for your users so they never type it. Users can still change it with
**Change** on the sign-in screen; you can also set it per PC with an environment variable:
```powershell
setx P2P_CLOUD_URL "http://iotgateway.live/"
```

---

### 7.1 Linux (Ubuntu) build

Build **on an Ubuntu machine** (PyInstaller cannot build Linux programs on Windows). The result runs on the same or
newer Ubuntu, so build on the oldest version you support (e.g. 22.04):
```bash
cd ftp_app
bash packaging/linux/build_linux.sh            # same built-in servers as the Windows build
```
Output in `dist/`: `FileJet` (single program), `filejet_2.0.0_amd64.deb` (installer with app-menu entry and icon),
`FileJet-linux-x86_64.tar.gz`. Install on a user's PC: `sudo apt install ./filejet_2.0.0_amd64.deb`.

Automatic builds: `.github/workflows/build-desktop.yml` builds the Windows exe and the Linux .deb (Ubuntu 22.04) on
GitHub – *Actions → Build desktop app → Run workflow*.

---

## 8. Deploy the server without Docker (Windows or Linux)

One program, **`serve.py`**, runs the cloud and the signaling server together and reads one settings file
(`server.env`). An installer sets everything up and makes it start automatically with the computer.

Choose the kind of deployment:

| | **A. Office network** (LAN) | **B. Internet with a domain** (e.g. iotgateway.live) |
|---|---|---|
| Users reach the server by | its IP, e.g. `http://192.168.1.20:8000/` | the domain, e.g. `http://iotgateway.live/` |
| Needs a domain | no | yes (HTTP; optional HTTPS with `--https` / `-Https`) |
| Ports to open on the server | TCP 8000, 8765, 8766 | TCP 80, 8766 (443 only with HTTPS) |
| Installer option | `-Address` / `--address` (or nothing = auto-detect) | `-Domain` / `--domain` |

Server size: 1 CPU / 1 GB RAM is enough for hundreds of users – files do **not** pass through the server.

### 8.0 Your server: iotgateway.live (AWS, Ubuntu, nginx) – plain HTTP

| | |
|---|---|
| Domain | **iotgateway.live** → `13.204.80.52` |
| Cloud URL for the app | **http://iotgateway.live/** (the app's default) |
| Signaling server | **ws://iotgateway.live:8765/ws** – health check: http://iotgateway.live:8765/healthz |
| Web dashboard | http://iotgateway.live/ |

**1. AWS security group** (EC2 → instance → *Security* → security group → *Edit inbound rules*) – allow:

| Type | Port | Source |
|---|---|---|
| HTTP | 80 | 0.0.0.0/0 |
| Custom TCP | 8765 | 0.0.0.0/0 (signaling server) |
| Custom TCP | 8766 | 0.0.0.0/0 (reflector for NAT hole punching) |

Port 8000 (Django) stays **closed** – nginx reaches it locally.

**2. Stop the old manual Django process** (currently running in debug mode):
```bash
sudo ss -ltnp | grep -E ':8000|:8765'      # shows what is running there
# stop it (e.g. sudo systemctl stop <old-service>; sudo systemctl disable <old-service>)
```

**3. Install FileJet** (copy the project to the server first, e.g. `scp -r ftp_app ubuntu@13.204.80.52:~/`):
```bash
cd ~/ftp_app
sudo bash deploy/linux/install.sh --domain iotgateway.live
```
The installer runs Django + signaling as the service **`mediarush`** (production mode, new random secrets) and,
because nginx is installed, configures the nginx site **`mediarush`** (`deploy/nginx/mediarush.conf`): `/ws` and
`/healthz` → signaling server, everything else → Django. Other enabled nginx sites for iotgateway.live are disabled
(backup kept).

**4. Check**
```bash
curl http://iotgateway.live:8765/healthz       # {"ok":true,...}            <- signaling server
curl http://iotgateway.live/api/config/        # "signaling_url":"ws://iotgateway.live:8765/ws"
systemctl status mediarush nginx
```

**5. Apps:** FileJet uses `http://iotgateway.live/` by default – just create an account and sign in.

> Plain HTTP: sign-in passwords and tokens travel unencrypted between the apps and the server. File data and chat
> are still encrypted end-to-end between the PCs. To add HTTPS later: run the installer again with `--https`.

### 8.1 Windows server (Windows 10/11 or Windows Server)

1. Install **Python 3.10+** from https://www.python.org/downloads/ – tick **“Add python.exe to PATH”**.
2. Copy the project folder `ftp_app` to the server (e.g. `C:\Setup\ftp_app`).
3. Open **PowerShell as Administrator** (right-click → *Run as administrator*) and run **one** of:
   ```powershell
   cd C:\Setup\ftp_app

   # A. office network (uses this PC's IP automatically; or add -Address 192.168.1.20)
   powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1

   # B. internet with your domain (point the domain's DNS A record to this server first)
   winget install CaddyServer.Caddy          # web proxy, once
   powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -Domain iotgateway.live
   ```
4. The installer:
   - copies the server to **`C:\FileJet`** and installs its Python packages,
   - writes **`C:\FileJet\server.env`** with new random secrets,
   - creates the database **`C:\FileJet\data\mediarush.sqlite3`**,
   - registers the task **“FileJet Server”** (starts at boot, restarts automatically if it stops) and starts it,
   - opens the Windows Firewall ports,
   - with `-Domain`: also starts Caddy (web proxy, plain HTTP; add `-Https` for a certificate).
5. At the end it prints the **Cloud URL** – give this to your users.
6. Optional admin account for `/admin/`:
   ```powershell
   & C:\FileJet\.venv\Scripts\python.exe C:\FileJet\serve.py --env C:\FileJet\server.env manage createsuperuser
   ```

Manage it (Administrator PowerShell):

| Task | Command |
|---|---|
| Status | `Get-ScheduledTask "FileJet Server"` |
| Stop / start | `Stop-ScheduledTask "FileJet Server"` / `Start-ScheduledTask "FileJet Server"` |
| Log | `Get-Content C:\FileJet\data\server.log -Tail 50 -Wait` |
| Change settings | edit `C:\FileJet\server.env`, then stop + start the task |
| Update to a new version | copy the new project folder, run `install.ps1` again (settings + database are kept) |
| Remove | `powershell -ExecutionPolicy Bypass -File deploy\windows\uninstall.ps1` (add `-RemoveFiles` to delete everything) |

> The computer running the server must stay **on** and must not sleep (*Power options → Sleep: Never*).

### 8.2 Linux server (Ubuntu / Debian)

1. Copy the project folder to the server: `scp -r ftp_app user@SERVER:~/`, then `ssh user@SERVER`.
2. Run **one** of:
   ```bash
   cd ~/ftp_app

   # A. office network (IP auto-detected; or --address 192.168.1.20)
   sudo bash deploy/linux/install.sh

   # B. internet with your domain (DNS A record -> this server first); nginx if installed, else Caddy
   sudo bash deploy/linux/install.sh --domain iotgateway.live
   ```
3. The installer copies the server to **`/opt/mediarush`**, writes **`/opt/mediarush/server.env`** with random secrets,
   creates the database in `/opt/mediarush/data`, installs the systemd service **`mediarush`** (starts at boot,
   restarts on failure), opens the firewall if `ufw` is active, and with `--domain` sets up nginx or Caddy
   (plain HTTP; add `--https` for a certificate).
4. Optional admin account:
   ```bash
   sudo -u mediarush /opt/mediarush/.venv/bin/python /opt/mediarush/serve.py --env /opt/mediarush/server.env manage createsuperuser
   ```

Manage it:

| Task | Command |
|---|---|
| Status / logs | `systemctl status mediarush` / `journalctl -u mediarush -f` |
| Restart (after editing `server.env`) | `sudo systemctl restart mediarush` |
| Update | copy the new project folder, run `install.sh` again (settings + database kept) |
| Remove | `sudo systemctl disable --now mediarush && sudo rm /etc/systemd/system/mediarush.service` |

### 8.3 Verify (both)

Open in a browser (use your Cloud URL):
- `http://192.168.1.20:8000/` or `http://iotgateway.live/` → FileJet sign-in page
- add `api/config/` to the URL → shows the `signaling_url`

Then in the FileJet app: sign-in screen → **Change** → enter the Cloud URL → create an account.
The status bar should show **● Online**.

### 8.4 E-mail (folder invitations)

Until SMTP is configured, invitation e-mails are **not sent** – they appear on the web under **Inbox** (and in the
log), so the folder ID is still available. To send real e-mails, edit `server.env`:
```ini
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.yourprovider.com
EMAIL_PORT=587
EMAIL_HOST_USER=you@yourdomain.com
EMAIL_HOST_PASSWORD=your-smtp-password
EMAIL_USE_TLS=1
DEFAULT_FROM_EMAIL=FileJet <no-reply@yourdomain.com>
SHOW_EMAIL_OUTBOX=0
```
then restart the server (Windows: stop + start the task; Linux: `sudo systemctl restart mediarush`).

### 8.5 Settings reference (`server.env`)

| Setting | Meaning |
|---|---|
| `DJANGO_SECRET_KEY`, `P2P_CLOUD_JWT_SECRET` | Random secrets (created by the installer). Keep them private. |
| `DJANGO_ALLOWED_HOSTS` | Names/IPs users use to reach the server (comma separated). |
| `DJANGO_HTTPS` | `0` = plain HTTP (default), `1` only if the site has an HTTPS certificate. |
| `CLOUD_PUBLIC_URL` | The Cloud URL (used for links in e-mails). |
| `P2P_SIGNALING_URL` | `ws://IP:8765/ws` (office) or `ws://iotgateway.live:8765/ws` (domain, the default). The app gets it from the cloud. |
| `DJANGO_SQLITE_PATH` / `DATABASE_URL` | SQLite file (default) or PostgreSQL `postgres://user:pass@host:5432/db`. |
| `CLOUD_PORT`, `SIGNAL_PORT`, `REFLECTOR_PORT` | 8000 / 8765 / 8766. |
| `EMAIL_*`, `DEFAULT_FROM_EMAIL`, `SHOW_EMAIL_OUTBOX` | E-mail, see 8.4. |
| `SITE_COMPANY` | Company name on the website (footer, About). Default `FileJet`. |
| `SITE_CONTACT_EMAIL` | Shows *Contact* and *Request a demo* buttons on the website when set. |
| `DOWNLOADS_DIR` | Folder with the files offered on `/download/` (default `cloud/downloads/`): put `FileJet.exe`, `filejet_*_amd64.deb`, `FileJet-linux-x86_64.tar.gz` there. |

**Public website:** `/` (home), `/features/`, `/pricing/` (prices come from the plans in the database – edit them in
`/admin/` → Plans), `/download/`, `/about/`. The signed-in dashboard is at `/dashboard/`.

Check the settings without starting the server: `python serve.py --env server.env --check`.

---

## 9. Deploy the server with Docker (optional)

Result: `http://iotgateway.live/` serves the web dashboard + API, `ws://iotgateway.live/ws`
the signaling server, behind Caddy (plain HTTP) with PostgreSQL.

### 9.1 What you need
- A Linux server (VPS) with a **public IP** – 1 vCPU / 1 GB RAM is enough for hundreds of users
  (files do not pass through it).
- A **domain name**, e.g. `iotgateway.live`, with an **A record** pointing to the server IP.
- **Docker** + **Docker Compose plugin** on the server:
  ```bash
  curl -fsSL https://get.docker.com | sh
  ```
- An **SMTP account** for e-mails (folder invitations, notifications) – e.g. your mail provider, SendGrid, SES.

### 9.2 Open the firewall
| Port | Protocol | Why |
|---|---|---|
| 80 | TCP | Web dashboard, API, WebSockets |
| 8766 | TCP | Reflector for NAT hole punching (must reach the server directly) |

Example (Ubuntu): `sudo ufw allow 80,8766/tcp`

### 9.3 Copy the project and configure
```bash
scp -r ftp_app user@SERVER:/opt/mediarush        # or git clone
ssh user@SERVER
cd /opt/mediarush
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # run 3 times for the 3 secrets
nano .env
```
Fill in `.env`:
| Variable | Value |
|---|---|
| `P2P_DOMAIN` | `iotgateway.live` |
| `DJANGO_SECRET_KEY` | random secret #1 |
| `P2P_CLOUD_JWT_SECRET` | random secret #2 (shared by cloud + signaling automatically) |
| `POSTGRES_PASSWORD` | random secret #3 |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` | your SMTP account |
| `DEFAULT_FROM_EMAIL` | e.g. `FileJet <no-reply@iotgateway.live>` |
| `BILLING_PROVIDER` | `dummy` (no payment gateway is included) |

### 9.4 Start
```bash
docker compose up -d --build
docker compose ps                                   # db, cloud, signaling, caddy should be "running"
docker compose exec cloud python manage.py createsuperuser
```

### 9.5 Verify
```bash
curl http://iotgateway.live/healthz           # {"ok":true,...}  (signaling via Caddy)
curl http://iotgateway.live/api/config/       # {"signaling_url":"ws://iotgateway.live/ws",...}
```
Open `http://iotgateway.live/` in a browser → FileJet sign-in page.
Admin area: `http://iotgateway.live/admin/`.

### 9.6 Connect the apps
In FileJet: sign-in screen → **Change** → `http://iotgateway.live/` → create account / sign in.
The status bar should show **● Online**.

---

## 10. Roll out to users

1. Build `FileJet.exe` ([section 7](#7-build-the-installable-app-filejetexe)) and share it (file share, intranet, e-mail link).
2. Tell users the **cloud URL** (`http://iotgateway.live/`, the app's default) – they enter it once via **Change** on the
   sign-in screen (or set `P2P_CLOUD_URL` for them).
3. Each user creates an account and sends their **ID** to whoever shares folders with them.
4. First transfer: allow FileJet in the Windows firewall prompt.
5. For best speed between offices/homes, enable **UPnP** on routers (or forward a TCP port); if two PCs cannot
   connect directly, FileJet says so – there is no relay by design (files never pass through a server).

---

## 11. Operate: update, back up, logs

| Task | Windows server | Linux server | Docker |
|---|---|---|---|
| Update | copy new files, run `install.ps1` again | copy new files, run `install.sh` again | `docker compose up -d --build` |
| Logs | `C:\FileJet\data\server.log` | `journalctl -u mediarush -f` | `docker compose logs -f cloud` |
| Restart | stop + start task "FileJet Server" | `sudo systemctl restart mediarush` | `docker compose restart` |
| Back up the database | copy `C:\FileJet\data\mediarush.sqlite3` (stop the task first) | copy `/opt/mediarush/data/mediarush.sqlite3` (stop first) | `docker compose exec db pg_dump -U mediarush mediarush > backup.sql` |
| Back up settings | `C:\FileJet\server.env` | `/opt/mediarush/server.env` | `.env` |

The database holds only metadata (accounts, folders, permissions, transfer records). **Files live on the users’ PCs**
– back those up with your normal PC/NAS backup.

App data on each PC (settings, outbox, received-files log, chat history): `%USERPROFILE%\.mediarush\` (Windows) /
`~/.mediarush/` (Linux/macOS).

---

## 12. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Red bar “offline” / **WinError 10061** | Signaling server not running → start it ([4.2](#42-terminal-2--signaling-server)) or `python run_dev.py`. |
| “rejected this app’s sign-in” | `P2P_CLOUD_JWT_SECRET` differs between cloud and signaling – use the same value, restart both. |
| “signed in elsewhere” | Same account opened twice. Use another account (or `--profile two` with a different account). |
| Web page: **Bad Request (400)** | Host not allowed – add it to `DJANGO_ALLOWED_HOSTS` (and `DJANGO_CSRF_TRUSTED_ORIGINS`). |
| No invitation e-mails | Development: see *Inbox* on the web. Production: check `EMAIL_*` in `.env`, then `docker compose logs cloud`. |
| Users see each other offline | They must be linked: one adds the other by ID in *Users*. Both apps must be signed in. |
| Files “Waiting for admin” forever | The folder owner’s app must be running and online. Delivery starts automatically. |
| Transfer cannot connect between two sites | Both behind strict/carrier NAT. Enable UPnP on one router or forward a TCP port to that PC; allow FileJet in the firewall. |
| No video thumbnails | Install ffmpeg and put it on the PATH (images work without it). |
| Layout looks wrong | *Settings → Reset layout*. |

---

## 13. Run the tests (developers)

```powershell
python -m pytest -q                       # app + signaling + end-to-end (cloud, transfers, resume, chat)
cd cloud; python manage.py test           # cloud
```
