# Greenlight Recording Tools

CLI utilities to audit, recover, and reassign BigBlueButton recordings created by Greenlight.

## Non-negotiable safety invariant

This project only considers a recording eligible when its reference `presentation/metadata.xml` explicitly identifies Greenlight as its origin (`bbb-origin=greenlight`, compared case-insensitively to support historical Greenlight metadata). Mutating operations apply the same check to every format-specific `metadata.xml` before changing anything.

The scanner is intentionally limited to:

- `/var/bigbluebutton/published/presentation/<recordID>/metadata.xml`
- `/var/bigbluebutton/unpublished/presentation/<recordID>/metadata.xml`
- `/var/bigbluebutton/deleted/presentation/<recordID>/metadata.xml`

Directory traversal follows symlinks. `presentation` is the discovery/index format only: mutating operations dynamically locate and update every format that has a matching `metadata.xml` under the corresponding state directory.

Development is in progress.
