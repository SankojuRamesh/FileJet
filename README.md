# FileJet

> **Step-by-step run & deployment guide: [docs/RUN_AND_DEPLOY.md](docs/RUN_AND_DEPLOY.md)**

**One desktop app** for sharing folders and sending files of any size and type **directly between computers**, plus a
small Django **cloud** that handles accounts and permissions and keeps **metadata only**.

* **Fast P2P.** File data always goes **straight from one computer to the other** over TLS 1.3 with parallel TCP streams.
  No server ever carries or stores file data: there is **no relay and no cloud storage**.
* **One app does everything.** Every user can:
  * create or open folders on their own PC or NAS,
  * add users by their 9-digit ID,
  * share a folder with one or many users with per-user permissions,
  * and, in the same app, work in the folders other people shared with them.
* **Metadata only in the cloud:** who sent what, to which folder, when, the size, the status and the speed. The cloud
  never sees file contents, paths or upload-form values.
* **Send even when the folder owner is offline.** Files wait in an **outbox on the sender's computer** and are delivered
  directly as soon as the owner's app is online, even if either app was restarted in between.
* **Big files, chunk by chunk, with resume.**
  * Files and folders of any size (10 GB and more) are sent in 8 MiB chunks over parallel TLS streams.
  * A chunk counts only after it is verified with SHA-256 and flushed to disk.
  * If the network drops or either PC loses power mid-transfer, the transfer continues from the last verified chunk.
* **Thumbnails.**
  * Images and videos get a preview automatically; video previews need ffmpeg installed.
  * The sender can also pick a thumbnail image.
  * Anything without a thumbnail shows a folder icon or a file-type icon (PDF, MP4, ZIP, DOCX…).
  * Thumbnails go end-to-end encrypted to the owner, never to the cloud.

Example: a studio manager shares the folder *Movie Project* with three editors. Two editors are *Uploaders*, so they
can send files into the folder. One editor is a *Manager*, so they can also download, rename and delete. The studio's
PC receives the files, and its *Received files* list shows who sent what and when. The web dashboard shows the same
transfers, but only their names, sizes, times and status.

```text
          ┌───────────── Cloud (Django) - metadata only ─────────────┐
          │ accounts · users by ID · folders + permissions · e-mails  │   <- web dashboard
          │ transfer records: file, size, from, to, time, status      │
          └──────▲───────────────────────────────────────▲────────────┘
                 │ REST (JWT)                  REST (JWT) │
     ┌───────────┴──────────┐   signaling / presence  ┌───┴──────────────────┐
     │ FileJet            │◄── (tiny, E2E-encrypted ►│ FileJet            │
     │ (studio manager)     │     control messages)    │ (editor)             │
     │ folder on THIS PC    │                          │ outbox on THIS PC    │
     └──────────┬───────────┘                          └──────────┬───────────┘
                ╚════ FILE DATA: direct P2P, TLS 1.3, parallel TCP streams ════╝
```

---

## 1. How it works

1. **Everyone installs the same app** and creates an account (in the app or on the web). Each account has a **9-digit
   ID**, shown in *Shared with me*.
2. **Add users by ID** (*Users* → Add user; a username or e-mail also works). Linked users see each other online.
3. **Share a folder** (*My Folders* → +). It can be a new folder or an existing one on this PC or a NAS. The cloud gives
   it a unique ID such as `FD-7K3M-9QX2`. Then use *Give access…* and pick a user **or a group**, a **role** and
   optionally an **expiry date**. The user gets an e-mail with the folder ID.
4. **The user sees the folder** under *Shared with me* and opens it with one click. They work in it strictly within
   their permissions, and the owner's app checks every single request.
5. **Sending files** (files or whole folders, any type):
   * **Owner online:** the files go straight to the owner's folder, with live progress on both sides.
   * **Owner offline:** the files are queued in the sender's outbox (*My uploads → Waiting for admin*), and the cloud
     shows them as *waiting*. When the owner opens the app, delivery starts automatically, resuming from the last
     verified chunk if it was interrupted.
6. **Records:**
   * **Owner's PC:** a *Received files* list with file, sender, size, time and form values. Each file can be opened or
     shown in its folder.
   * **Cloud and web:** transfer metadata and per-folder statistics: files, total size, waiting, senders, last upload.
   * **E-mail:** the owner gets one message per completed batch.

### Permissions

| Role | View & download | Upload (send) | Edit (rename, new folder) | Delete |
|---|:-:|:-:|:-:|:-:|
| **Uploader** | – | ✓ | – | – |
| **Viewer** | ✓ | – | – | – |
| **Editor** | ✓ | ✓ | ✓ | – |
| **Manager** | ✓ | ✓ | ✓ | ✓ |
| **Custom** | any combination | | | |

Changes apply **immediately**, including to running transfers. Expired access is refused.

### Folder features (Media Shuttle-style)

| Feature | What it does |
|---|---|
| **Share folder** | Users browse, upload and download (as permitted). |
| **Submit folder** | A drop box: uploaders send files in but cannot see the contents. |
| **File rules** | Optional allowed types and maximum size. The default is any type and any size. The rules are checked before sending and again by the owner's app. |
| **Upload form** | Fields such as *Project\** or *Notes*. The values are saved only on the owner's PC (`file.metadata.json` and the *Received files* list) and never in the cloud. |
| **Groups, expiry, notifications, activity log, organization name** | As in Media Shuttle. |
| **Storage** | The owner's own disk or NAS. Free space is shown, and uploads that don't fit are refused before they start. |

---

## 2. Quick start (one computer)

```powershell
py -m venv .venv ; .venv\Scripts\activate
pip install -r requirements.txt
python run_dev.py --apps     # cloud + signaling server, and the app opened twice (profiles "one" and "two")
```
* Sign in to the two windows with **two different accounts**. One account can be online in only one app at a time.
* Development e-mails are shown on the web at http://127.0.0.1:8000/ → **Inbox**.
* To start the app on its own, run `python filejet.py`. For a second profile on the same PC, run
  `python filejet.py --profile two`.

### Installable app
```bash
pip install pyinstaller
python build_desktop.py      # dist/FileJet(.exe) - the desktop app
```
The servers are not inside the executable. For testing, use `run_dev.py`; for production, deploy them (§5).

---

## 3. The app

**Design and layout**
* Modern dark design:
  * layered neutral surfaces with an indigo-blue accent;
  * rounded buttons, fields, tables and cards;
  * a pill highlight for the active navigation icon, and initials avatars with online badges;
  * underline tabs, thin scrollbars, and a quiet status bar.
* **Resizable panels, like VS Code:**
  * Drag the line between the side bar and the main area, or between the top and bottom panels, with the left mouse
    button. The line lights up blue on hover and while dragging.
  * **Double-click** the line to collapse or restore that panel.
* **Keyboard and clicks:**
  * **Ctrl+B** hides or shows the side bar; clicking the active icon on the left does the same.
  * **Ctrl+J** hides or shows the bottom panel. **Ctrl+1…6** switch views.
* **Table columns** are resizable too: drag a header divider, or double-click it to fit the content.
* **Remembered layout:** panel sizes and the window size and position are saved per profile in `layout.json`.
  *Settings → Reset layout* restores the defaults.

* **My Folders (shared by me):**
  * Your folders, with a file browser, Copy ID, Settings, Delete, free space and statistics.
  * **Users with access:** roles, the View/Upload/Edit/Delete checkboxes, expiry, resending the invitation.
  * **Received files:** open a file or show it in its folder; sender, size, time and form values.
  * **Waiting to arrive:** files users sent while you were offline.
* **Shared with me:**
  * Folders others shared with you, grouped by owner, with an online dot and `[V U E D]` tags.
  * A permission row shows ✔/✘ for **View & download · Upload · Edit · Delete**.
  * The action buttons are always visible: **Send files · Send folder · Receive / Download · New folder · Rename ·
    Delete**. Anything your role doesn't allow is greyed out, and its tooltip says why.
  * *Send* opens a dialog with previews of what you're sending, the owner's upload form and an optional thumbnail.
  * **My uploads:** thumbnail or type icon, *Waiting for admin*, *Sending* with % and speed, *Delivered*, *Failed*
    (Retry) and *Cancel*.
* **Users:** add users by ID, see who is online, manage groups. Each user has a **Chat** button.
* **Chat:**
  * Everyone you are linked with, both people you added and people who added you.
  * Each person shows a live **online/offline** dot, an unread count and their last message.
  * Conversations have ticks: ⏱ waiting, ✓ delivered, ✓✓ read.
  * Messages are **end-to-end encrypted and go directly between the two apps**. A message to someone who is offline
    waits on your PC and is delivered when they come online.
  * Chat history stays on the two computers and never goes to the cloud.
  * New messages show in the status bar and the Chat icon turns orange.
* **Sizes and progress bars everywhere,** while a transfer runs and afterwards:
  * Bars show the size inside them, for example `4.2 GB / 10.7 GB · 39%`. They turn green when done and red on
    failure.
  * **Transfers:** total bars for everything **↑ Sending** and **↓ Receiving** (files, done, left, speed, time left),
    plus per-transfer columns Files, Size, Progress bar, Left, Speed and Time left. After a transfer, you still see
    its size, how long it took and the average speed.
  * **Shared with me → My uploads:** a summary bar and a bar per file, plus waiting and delivered totals in MB/GB.
  * **My Folders:** an **Arriving now** bar showing who is sending what (2/5 files, 1.2 GB of 4 GB, speed, time left).
    The receiver knows the whole batch size before later files start. The *Received files* and *Waiting to arrive*
    tabs show counts and total size.
  * The Send dialog lists each file or folder with its size and the total.
* **Transfers:**
  * **↑ Sending** and **↓ Receiving** for everything: what you send or download, and what others send into your folders
    or download from them.
  * Filters (*All / Sending / Receiving*), plus progress, speed, connection and thumbnails.
  * The cloud history marks each record *Sent by me* or *Received*.
  * Also **Account** and **Settings**.

**Web dashboard:**
* **Dashboard:** live transfers.
* **Transfers:** full history, with file, size, from, to, status, speed and time.
* **Folders:** per-folder statistics, *Files sent to this folder* (including waiting ones), members and activity, plus
  *Files I sent*.
* **Users**, **Devices**, **Subscription** and **Account**.

The web shows names and status only. Files can be opened only in the desktop app, on the computer that has them.

---

## 4. Security and performance

| Layer | Mechanism |
|---|---|
| Accounts | Django auth, JWT (15 min) with rotating refresh tokens, throttling. |
| Access | Only users you added by ID can get access. Opening a folder works only for the invited account. Permissions and expiry are enforced by the **owner's app** on every request, and paths are confined to the folder. |
| Devices | Per-install ECDSA P-256 key; only the certificate is registered. Devices can be revoked on the web. |
| Signaling / presence | Signed-in users only (cloud token plus device signature), and only between linked users. Control messages are end-to-end encrypted (ECDH, HKDF, AES-256-GCM). |
| File data | Direct only. TLS 1.3 with **pinned** device certificates, a per-transfer HMAC, per-chunk SHA-256, and an atomic `.part` → final rename. |
| Cloud data | Metadata only, never file contents, local paths or form values. |

**Connection path.**
* Direct: LAN, IPv6, UPnP port mapping, or a STUN-discovered public address.
* If that fails: **TCP hole punching**.
* If both fail: the app says so and asks you to enable UPnP or forward a port. Nothing falls back to a server.

**Reliability.**
* Resume from the last `fsync`ed chunk.
* Automatic reconnect after sleep or a network change.
* Outbox delivery survives restarts of either app.

**Measured throughput.** Both apps ran on one PC, so these numbers measure the app, not a network. The source was
flushed to disk first.

| Test | Result |
|---|---|
| 3 GB file | **355 MB/s** (about 2.8 Gbit/s), peak 538 MB/s |
| 10 GB file | about 87 MB/s |

**Why the 10 GB file is slower:** the test PC's QLC SSD writes at about 1 GB/s until its cache fills after roughly
9 GB, then at 70–140 MB/s. Between two computers the sender only reads and the receiver only writes, so large files
are limited by the receiving disk and the network.

Other notes:
* Chunks that arrive out of order are written in file order. On Windows/NTFS this avoids a zero-fill slowdown
  (68 → 87 MB/s on the same disk).
* RAM stays under 512 MB for any file size.
* Useful tools:
  * `bench/benchmark.py --size 10G --trace 10 [--dest-dir D:/]` measures a transfer.
  * `bench/netem.sh` simulates WAN conditions on Linux.

---

## 5. Production deployment

Full step-by-step guide: **[docs/RUN_AND_DEPLOY.md](docs/RUN_AND_DEPLOY.md)**.

**Without Docker (recommended).** One installer sets up the server and starts it automatically at boot.
`serve.py` runs the cloud and the signaling server together.

```powershell
# Windows server, in an Administrator PowerShell
powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1                         # office network
powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -Domain iotgateway.live
```
```bash
# Linux server (Ubuntu/Debian)
sudo bash deploy/linux/install.sh                                   # office network
sudo bash deploy/linux/install.sh --domain iotgateway.live     # internet, http://iotgateway.live/
```

**With Docker (optional).**
```bash
cp .env.example .env && docker compose up -d --build
```

* Users enter the Cloud URL the installer prints (e.g. `http://192.168.1.20:8000/` or `http://iotgateway.live/`)
  via **Change** on the sign-in screen.
* Ports to open: office network TCP 8000, 8765 and 8766; domain TCP 80 and 8766.

Main REST endpoints:

| Endpoint | Purpose |
|---|---|
| `api/auth/…`, `api/me/` | Accounts |
| `api/devices/`, `api/signal-token/` | Devices and the signaling token |
| `api/folders/` | Everything the signed-in user owns, plus what is shared with them, with statistics |
| `api/folders/clients/` | Users |
| `api/folders/create/` | New folder |
| `api/folders/<FD-ID>/` | Folder settings |
| `…/members/` | Members and access |
| `…/files/` | Files sent to the folder (metadata) |
| `…/activity/` | Activity log |
| `api/folders/join/` | Open a folder by ID |
| `api/transfers/report/`, `api/transfers/authorize/`, `api/transfers/` | Transfer metadata and history |
| `api/billing/…` | Plans (`BILLING_PROVIDER=dummy`) |

---

## 6. Tests

```bash
python -m pytest -q                    # 15 tests + 1 skipped on Windows (symlinks need developer mode)
cd cloud && python manage.py test      # 19 tests
```

`tests/test_cloud_flow.py` runs a real cloud, a real signaling server and several app cores. It covers:
* adding users by ID, and presence;
* a Submit folder: the invitation e-mail, a stranger who knows the ID being refused, file-type, size and form rules,
  the metadata sidecar, the *Received files* log, and the upload notification;
* the roles (edit and delete), expiry, and revoking access during a running transfer;
* groups;
* **sending while the owner is offline**: queued, shown in the cloud as waiting, then delivered when the owner comes
  back, and shown in the owner's Transfers as one *Receiving* batch;
* **chat**: linked users in both directions, delivered and read ticks, a stranger refused, and a message to an offline
  user waiting until they are back;
* **resume after power or network loss**: a 64 MB file is interrupted twice, once by the sender's PC and once by the
  owner's PC. Each time it continues from the last verified chunk, not from zero, and ends bit-identical.

A scripted run of the real windows passes 43/43 steps. It covers:
* live progress bars with sizes on both sides during a slowed-down transfer;
* resizing panels with real mouse drags, double-click collapse and restore, Ctrl+B, the active icon toggle, and saved
  sizes;
* chatting through the Users → Chat button and Enter-to-send, with unread badges, read ticks, and a message queued
  while offline;
* two users in the same app, both sharing folders;
* Uploader buttons (send enabled; rename, delete and receive visible but disabled), then Manager buttons (all
  enabled);
* the owner going offline, the send being queued and then delivered;
* thumbnails for the image and the video, type icons for the rest;
* *Received files*, and *↓ Receiving* / *↑ Sending* in Transfers;
* cloud statistics without any thumbnail or path.

## 7. Project structure

```text
ftp_app/
├── filejet.py            the desktop app            run_dev.py   one-command local stack
├── client/
│   ├── gui/              window, login, admin_view (My Folders, Users), client_view (Shared with me, My uploads),
│   │                     views (Transfers, Account, Settings), theme, icons, filetable
│   ├── core.py           AppCore: session, users, folders, members, presence, outbox, received log, jobs, reporter
│   ├── shares.py         local folders/members/outbox/received log, permission checks, rules, path confinement
│   ├── presence.py, e2e.py, cloud_api.py
│   └── sender.py, receiver.py, session.py, transport.py, nat.py …   (P2P engine)
├── server/               signaling (rendezvous), presence hub, reflector
├── cloud/                Django: accounts, billing, transfers, sharing
└── tests/  bench/  deploy/  Dockerfile  docker-compose.yml  .env.example
```

## 8. Known limitations

* A queued file is delivered while **both** apps are running at the same time: the files are on the sender's PC and the
  folder is on the owner's PC, and no server holds them in between.
* The performance numbers are from loopback; there is no WAN benchmark yet.
* Behind symmetric or carrier-grade NAT on both sides, a direct connection may be impossible without UPnP or a
  forwarded port, because there is no relay by design.
* Many tiny files are slower than one archive, since each file sets up its own connection.
* Payments are not integrated (a dummy provider is used).
