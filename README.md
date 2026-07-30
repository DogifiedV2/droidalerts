# Droid Alerts

Droid Alerts watches the Droid Tycoon chat while you play and tells you the
moment a rare droid spawns with a sound, a popup on screen, and (if you want)
a notification on your phone. No more staring at the chat box or missing a
Mythic because you tabbed out.

Out of the box it alerts on these priority spawns:

- **Beskar Mythic**
- **Rainbow Mythic**
- **Diamond Mythic**
- **Beskar Legendary**
- **Beskar Epic**
- **Galactic Mythic**
- **Galactic Legendary**
- **Galactic Epic**

**Rainbow Epic** and **Rainbow Legendary** are also available as
priority-alert options and are off by default. **Galactic Common** and
**Galactic Rare** are available but also start off. Use **Modify** on the
Dashboard to manage the full droid-by-rarity grid.

It works on most PCs and starts with a recommended chat region. Everything it
saves stays inside its own folder. Screenshots remain local unless you
explicitly enable a phone-notification attachment or **Share alert debug
screenshots with the developer** in Advanced settings.

## How to install (Windows)

1. Download the latest `DroidAlerts.zip` from the GitHub Releases page.
2. Unzip it anywhere you like. Your Desktop is fine.
3. Open the `Droid Alerts` folder and double-click `Droid Alerts.exe`.

That's it. Choose the display with Fortnite, click **Select Window**, or use
**Select Capture Device** for a console connected through a USB capture card.
Then click **Start Watching**, go play, and it will ping you when something good
spawns. Window capture keeps reading Fortnite when another window covers it;
keep Fortnite restored because Windows may pause capture while it is minimized.
Use the **Test Alert** button any time to hear and see what an alert looks like.

The Dashboard shows the selected monitor, window, or capture device used by
both watchers, priority-alert choices, alert-channel controls, current timers,
and session counts. **Show Chat Region** and **Move Chat Box** are under
Diagnostics; moving the box never resizes it. Alert
appearance plus less common detection, debug, notification-detail, and storage
controls stay behind the **Advanced settings** toggle in Settings.

### Rebirth Ready alerts

Enable **Rebirth Ready** under Dashboard → Priority Alerts to receive one alert
when the large green `READY!` HUD message appears. Droid Alerts checks the lower
part of the selected Fortnite display, window, or capture device every five
seconds and confirms the same reading twice before alerting. It also reads the
small Rebirth level beside the green circular-arrow icon, so a persistent
`READY!` message only alerts once for that level. The next level is eligible for
a new alert, and the last alerted level is remembered after restarting the app.

The alert uses the normal Popup, Sound, Discord, ntfy, and Pushover channels and
appears in History as **Rebirth Ready**. The scan interval can be changed from
2–30 seconds under Settings → Advanced settings → Detection & Timing; five
seconds is recommended.

The fixed sidebar keeps Dashboard, Belt Tracker, Limited Deals, History,
Diagnostics, and Settings available in one place. The interface uses one
consistent Signal dark theme across the main window, dialogs, alert popups,
region tools, and timer overlay.

## Belt Tracker

The **Belt Tracker** tab watches the moving blueprint belt and can alert from a
different minimum rarity tier for each droid. It can run at the same time as
chat alerts.

1. Choose the Fortnite display, window, or capture device once on the Dashboard.
   Both watchers use that capture source.
2. Open Belt Tracker and click **Select Belt Region**.
3. Use the recommended and only officially supported setup shown in the guide:
   stand at the start of the belt and match the cyan example box with two
   complete blueprint cards visible. Price labels may be inside the box; Belt
   Tracker ignores them. Other camera angles, distances, and framing may not
   detect reliably. Press Enter to save it.
4. Under **Priority Alerts**, click **Modify**, search for a droid, and assign
   its minimum alert tier. You can edit several selected rows at once. A droid
   set to Off has no Belt Tracker alerts.
5. Click **Start Tracking**.

The detector identifies cards from a compact local artwork-template index and
does not use OCR. A dark-nameplate gate and confidence
margin reject scenery, ambiguous lookalikes, and incomplete edge cards. The
frame/label supplies the rarity tier (Default, Gold, Diamond, Rainbow, Beskar,
or Galactic). Common, Rare, Epic, Legendary, or Mythic is a fixed class for each
droid and comes directly from the bundled identity table; the colored pill is
not scanned.
Normally a card is reported as soon as four recent scans agree. Active regions
scan at up to 8 FPS and empty regions back off to 4 FPS. Minimum rarity tiers follow
Default → Gold → Diamond → Rainbow → Beskar → Galactic, so Gold+ includes Gold
and every tier above it. Galactic is selectable now; its visual templates will
be trained from real blueprint cards after release. The fixed Common–Mythic class does not affect this filter. Configured
droids use the same Alert Channels as Dashboard alerts. Saved Belt Tracker
regions are separate for each display, and its events appear with other alerts
in History.

Advanced Settings exposes **Belt idle scan FPS** and **Belt active scan FPS**.
Both accept 1–20 FPS, default to 4/8 for every install, and the idle value is
automatically kept at or below the active value. Higher active rates confirm
cards sooner but use proportionally more CPU.

To collect examples for detector review, keep two complete blueprints visible
in the officially supported framing and enable **Save detections for review**
under **Settings → Advanced settings → Belt Developer Tools** before starting
Belt Tracker.
Tracking stays on the same fast template path. It retains the sharpest fully
visible crop from each confirmed appearance under
`data/belt_template_samples/detections`, rejects near-duplicates, and keeps at
most 20 diverse samples per detected droid. Each JSON sidecar records the
detected name and rarity tier plus the identity's fixed class. These results never
enter `confirmed/` and do not alter the live template index automatically.

If Belt Tracker is unusually slow or misses cards, enable **Developer logging**
under **Belt Developer Tools** before starting it. Developer logging records
detector-stage timings, candidate rejection
reasons, tracker state, and one compressed belt-region frame per second under
`data/belt_dev`. Entered and ambiguous results also receive event evidence, and
each session stops adding images at 200 MB. Stop after reproducing the issue and create a Support Bundle in
Diagnostics; the bundle includes the latest Belt dev log and up to four frames.
If the bundled template index is unavailable or corrupt, Belt Tracker reports
the problem instead of silently switching detectors.

## Limited Deals

The **Limited Deals** page shows the offer that is live right now and can alert
through the same Popup, Sound, Discord, ntfy, and Pushover channels used by the
Dashboard. Familiar priority combinations, Galactic Epic/Legendary, plus every Mythic mutation are
available as quick toggles. For finer control, click **Modify** and choose a minimum deal tier for
any rotating droid; higher tiers also match through Default → Gold → Diamond →
Rainbow → Beskar → Galactic.

The app never downloads a schedule or future offers. It caches the current
hour locally, shows that cache while starting, and always refreshes the live
deal once when the app opens. While the app remains open, each new hour is
checked at `HH:00:10`, ten seconds after the offer changes.

Optional behaviour includes automatic watcher startup and pausing while
Fortnite is closed. Diagnostics can create a redacted support ZIP, show or
reposition the chat region, report local disk usage, and clear debug captures
or history. With automatic update checks enabled, the app checks every 15
minutes and shows a clickable **Update ready** action in the page header when a
new release is available. After an update, existing installations see the new
version's **What's New** notes once; fresh installations skip that update popup.

If there is no release zip yet, use the source version instead:

1. Install Python from [python.org/downloads](https://www.python.org/downloads/).
   Tick **Add python.exe to PATH** in the installer.
2. Download this repository as a ZIP and unzip it.
3. Double-click `Start Droid Alerts.bat`.

## Rebirth Alert

The Dashboard's **Priority Alerts** section includes a **Rebirth Alert**
toggle. It watches for the notification on the right side of the game when a
rebirth droid is available. Turn it on, then start the normal Dashboard watcher.
It uses the selected capture source and the existing popup, sound, Discord,
ntfy, and Pushover alert channels. The detector scans a small right-side box
aligned to the saved chat-alert height and confirms it across consecutive scans
before alerting.

## Get alerts on your phone

The best part: Droid Alerts can ping your phone even when you're away from the
PC. There are two options. Pick whichever you like. Both have a **Set Up**
button in the app that walks you through everything step by step and sends a
test alert to make sure it works.

### Pushover

Pushover is a paid app (one-time purchase after a free 30-day trial) with very
reliable, loud notifications.

1. Go to [pushover.net](https://pushover.net), create an account, and install
   the Pushover app on your phone.
2. In Droid Alerts, click **Set Up Pushover**. It shows you exactly where to
   find your User Key and how to create an API Token. Just copy and paste
   them in.
3. It sends a test alert to your phone so you know it's working.

### ntfy

ntfy is completely free and takes about two minutes.

1. Install the free **ntfy** app from the App Store or Google Play.
2. In Droid Alerts, click **Set Up ntfy**. It tells you what to do: subscribe
   to a made-up topic name in the app, type the same name into Droid Alerts,
   done.
3. It sends a test alert to your phone so you know it's working.

There's also a **Discord** option if you'd rather have alerts posted into a
Discord channel. The **Set Up Discord** button walks you through that too.

## Anonymous stats telemetry

Droid Alerts sends a small anonymous app heartbeat to `gonk.tools` while the
program is open so the site can total usage time. It contains a random anonymous
install ID, a per-run session ID, and the app version. The chat watcher has a
separate heartbeat while it is running so the site can show how many people are
currently watching droids. Its selected priority-alert combinations are included
on the first heartbeat and only sent again after they change.

When a priority alert fires, it also sends the timestamp plus the detected
droid/rarity combo so the site can count which rare droids are being found. Belt
Tracker has a separate heartbeat while it is running. It periodically sends
only confirmed droid names and compact cumulative counts grouped by anonymous
session and hour. Raw detector details, confidence values, boxes, and exit events are
not uploaded. Failed belt-count uploads stay in a small local retry file.

Normal telemetry does **not** send screenshots, player names, notification
credentials, machine names, chat text, or raw Belt Tracker frames.
Locally collected Belt template samples are never uploaded.

In **Settings**, **Identify This Install** shows the random install ID and links
to <https://gonk.tools/identify>. Identification is optional and requires a
Discord login plus a username chosen by the user. It is visible only in the
developer's protected stats view; public stats remain aggregate-only.

Debug mode also has a separate **Share alert debug screenshots with the
developer** option.
It is off by default and only appears while debug mode is enabled. If you turn it
on, alert detections upload the two debug screenshots for that alert, the
anonymous install/session IDs, app version, detected droid/rarity, monitor
resolution, capture region, and detector scale metadata to help fix false flags.
The separate ntfy and Pushover attachment switches send only the matching alert
crop to that notification provider; both are clearly labelled in Advanced
settings.

## Something not working?

- **No alerts coming through?** Make sure the game is on the monitor Droid
  Alerts is watching and the chat box is visible.
- **Capture card unavailable?** Close OBS or the card's preview application if
  it has exclusive control, reconnect the card, then refresh **Select Capture
  Device**.
- **Test alert works but nothing in-game?** Give it a moment. It checks the
  chat several times a second, but spawns are random.
- Anything beyond that, and for power-user features (command line, custom
  regions, debug mode), see [EXTRA.md](EXTRA.md).
