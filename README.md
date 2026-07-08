# Droid Alerts

Droid Alerts watches the Droid Tycoon chat while you play and tells you the
moment a rare droid spawns with a sound, a popup on screen, and (if you want)
a notification on your phone. No more staring at the chat box or missing a
Mythic because you tabbed out.

Out of the box it alerts on the five spawns worth dropping everything for:

- **Beskar Mythic**
- **Rainbow Mythic**
- **Diamond Mythic**
- **Beskar Legendary**
- **Beskar Epic**

It works on most PCs. It finds the chat box by itself, no setup needed.
Everything it saves stays inside its own folder, and the screenshots it takes
of the chat corner never leave your PC.

## How to install (Windows)

1. Download the latest `DroidAlerts.zip` from the GitHub Releases page.
2. Unzip it anywhere you like. Your Desktop is fine.
3. Open the `Droid Alerts` folder and double-click `Droid Alerts.exe`.

That's it. Click **Start** in the app, go play, and it will ping you when
something good spawns. Use the **Test Alert** button any time to hear and see
what an alert looks like.

If there is no release zip yet, use the source version instead:

1. Install Python from [python.org/downloads](https://www.python.org/downloads/).
   Tick **Add python.exe to PATH** in the installer.
2. Download this repository as a ZIP and unzip it.
3. Double-click `Start Droid Alerts.bat`.

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

## Anonymous watcher count

Droid Alerts has a **Share anonymous data** option, enabled by default. While the
watcher is running it sends a small heartbeat to `gonk.tools` so the site can
show how many people are currently watching droids. The heartbeat contains only
a random anonymous install ID, a per-run session ID, and the app version.

When a priority alert fires, the same anonymous-data setting also sends the
timestamp plus the detected droid/rarity combo so the site can track which rare
droids are being found. It still does **not** send screenshots, player names,
notification settings, machine names, or chat text. Turn it off any time in
**Settings → Options**.

Debug mode also has a separate **Share detections with the developer** option.
It is off by default and only appears while debug mode is enabled. If you turn it
on, alert detections upload the two debug screenshots for that alert, the
anonymous install/session IDs, app version, detected droid/rarity, monitor
resolution, capture region, and detector scale metadata to help fix false flags.

## Something not working?

- **No alerts coming through?** Make sure the game is on the monitor Droid
  Alerts is watching and the chat box is visible.
- **Test alert works but nothing in-game?** Give it a moment. It checks the
  chat several times a second, but spawns are random.
- Anything beyond that, and for power-user features (command line, custom
  regions, debug mode), see [EXTRA.md](EXTRA.md).
