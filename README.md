# Greenlight Recording Tools

CLI utilities to audit, recover, and reassign BigBlueButton recordings created by Greenlight.

## Non-negotiable safety invariant

This project only considers a recording eligible when its reference `presentation/metadata.xml` explicitly identifies Greenlight as its origin (`bbb-origin=greenlight`, compared case-insensitively to support historical Greenlight metadata). Mutating operations apply the same check to every format-specific `metadata.xml` before changing anything.

The scanner is intentionally limited to:

- `/var/bigbluebutton/published/presentation/<recordID>/metadata.xml`
- `/var/bigbluebutton/unpublished/presentation/<recordID>/metadata.xml`
- `/var/bigbluebutton/deleted/presentation/<recordID>/metadata.xml`

Directory traversal follows symlinks. `presentation` is the discovery/index format only: mutating operations dynamically locate and update every format that has a matching `metadata.xml` under the corresponding state directory.

## Installation

Run as root:

```bash
curl -fsSL https://raw.githubusercontent.com/BBB-Plugin/greenlight-recording-tools/main/install.sh | bash
```

The installer clones the repository into `/opt/greenlight-recording-tools` and creates:

```text
/usr/local/bin/greenlight-recordings
```

No third-party Python packages are required.

## Greenlight access

Commands that need to know whether a Room exists, or need a destination Room, query Greenlight through Rails inside the local Docker container. The default container name is:

```text
greenlight-v3
```

Override it when necessary:

```bash
export GREENLIGHT_CONTAINER=my-greenlight-container
```

## Read-only commands

Full provenance report, including detail by observed historical room name and a consolidated meetingID view:

```bash
greenlight-recordings rooms
```

Add current Greenlight Room existence checks:

```bash
greenlight-recordings rooms --check-greenlight
```

Only meetingIDs represented by recordings but no longer present as Greenlight Rooms:

```bash
greenlight-recordings orphans
```

Inspect one recording and all formats currently found for it:

```bash
greenlight-recordings inspect <recordID>
```

## Reassignment

Move one recording to an existing destination Greenlight Room:

```bash
greenlight-recordings move-recording <recordID> --to <destination-meetingID>
```

Move all eligible recordings belonging to one or several historical meetingIDs:

```bash
greenlight-recordings move-room <source-meetingID[,source-meetingID,...]> --to <destination-meetingID>
```

Before a modification, every format-specific metadata file found for the recording must also explicitly have `bbb-origin=greenlight`. If any format fails that invariant, the operation is aborted.

The reassignment derives a new BigBlueButton internal recording ID as `SHA1(destination meetingID)-timestamp`, renames every discovered format directory, updates the corresponding metadata, and preserves the timestamp portion of the original recording ID.

## Deleted recordings

`deleted` is intentionally included in discovery and normalization. A recording that remains under BigBlueButton's `deleted` state is not expected to become visible in normal Greenlight recording listings solely because its room association was normalized. Restoring recording state is a separate concern and is not performed implicitly by this tool.
