WorkOnward Read - Read this before your first launch
=====================================================

Thanks for downloading WorkOnward Read!

WorkOnward Read is based on CoverUP by Björn Seipel (digidigital), GPL-3.0.

INSTALL
-------
1. Drag "WorkOnward Read.app" onto the "Applications" shortcut in this window.
2. Eject the disk image.

FIRST LAUNCH ("app can't be opened" / "unidentified developer")
---------------------------------------------------------------
WorkOnward Read is free, open-source software (GPL-3.0). We do not have a
paid Apple Developer account, so the app is not notarized by Apple. macOS
Gatekeeper will therefore warn you the first time you open it. The source
code is public if you want to verify what you are running (the original
CoverUP project lives at https://github.com/digidigital/CoverUP).

To open the app the first time:

  Option A (macOS 13 / 14):
    1. Open your Applications folder in Finder.
    2. Right-click (or Control-click) "WorkOnward Read.app" and choose "Open".
    3. In the dialog that appears, click "Open".
    macOS remembers this choice - later launches work normally.

  Option B (macOS 15 Sequoia and newer):
    On macOS 15+ the right-click trick may no longer show an "Open" button:
    1. Double-click "WorkOnward Read.app" once (it will be blocked - that's expected).
    2. Open System Settings -> Privacy & Security.
    3. Scroll down to the Security section; you will see a message that
       "WorkOnward Read" was blocked. Click "Open Anyway".
    4. Confirm with your password or Touch ID.

  Option C (Terminal, for advanced users):
    Remove the quarantine flag that macOS attaches to downloaded files:

        xattr -d com.apple.quarantine "/Applications/WorkOnward Read.app"

    Then launch the app normally.

You only need to do this once. Updates installed by replacing the app
will trigger the warning again.

VERIFYING YOUR DOWNLOAD (optional)
----------------------------------
Every GitHub release includes a SHA256SUMS.txt file. Compare it against:

    shasum -a 256 <downloaded .dmg>

QUESTIONS?
----------
Website: https://workonward.org
Upstream project (CoverUP): https://github.com/digidigital/CoverUP
