# Greenlight Recording Tools

CLI utilities to audit, recover, reassign, and safely delete BigBlueButton recordings created by Greenlight.

## Non-negotiable safety invariant

This project only considers a recording eligible when its reference `presentation/metadata.xml` explicitly identifies Greenlight as its origin (`bbb-origin=greenlight`, compared case-insensitively to support historical Greenlight metadata). Mutating recording operations apply the same check to every format-specific `metadata.xml` before changing anything.

The scanner is intentionally limited to:

- `/var/bigbluebutton/published/presentation/<recordID>/metadata.xml`
- `/var/bigbluebutton/unpublished/presentation/<recordID>/metadata.xml`
- `/var/bigbluebutton/deleted/presentation/<recordID>/metadata.xml`

Directory traversal follows symlinks. `presentation` is the discovery/index format only: mutating operations dynamically locate and update every format that has a matching `metadata.xml` under the corresponding state directory.

## Installation / update

Run as root:

```bash
curl -fsSL https://raw.githubusercontent.com/BBB-Plugin/greenlight-recording-tools/main/install.sh | bash
```

The installer clones or updates the repository in `/opt/greenlight-recording-tools` and creates:

```text
/usr/local/bin/greenlight-recordings
```

No third-party Python packages are required.

## Greenlight access

Commands that query or modify Greenlight use Rails inside the local Docker container. The default container is:

```text
greenlight-v3
```

Override it when necessary:

```bash
export GREENLIGHT_CONTAINER=my-greenlight-container
```

## Recording provenance and orphan Rooms

Full provenance report, including detail by observed historical room name and a consolidated meetingID view:

```bash
greenlight-recordings rooms
```

Mark whether each historical meetingID still exists as a current Greenlight Room:

```bash
greenlight-recordings rooms --check-greenlight
```

Show only historical meetingIDs represented by recordings but missing from current Greenlight Rooms:

```bash
greenlight-recordings rooms --check-greenlight --missing-only
```

`--missing-only` implies the Greenlight existence check, so this shorter form is also valid:

```bash
greenlight-recordings rooms --missing-only
```

Equivalent compact orphan report:

```bash
greenlight-recordings orphans
```

## BigBlueButton internal meeting IDs

`bbb-record --delete` expects a BigBlueButton internal meetingID, not the Greenlight/external meetingID. Historical recordings may no longer be resolvable by `bbb-record --tointernal`, so this CLI reads the internal ID directly from each eligible Greenlight `metadata.xml`.

List delete-compatible internal IDs for one or several Greenlight/external meetingIDs:

```bash
greenlight-recordings bbb-record-ids <meetingID[,meetingID,...]>
```

The command is read-only. It prints the recording state, external meetingID, room name, internal meetingID, recordID, and the corresponding `bbb-record --delete <internal-meetingID>` command. It never executes the delete.

## Delete historical/orphan recordings

Delete all eligible Greenlight recordings belonging to one or more historical meetingIDs:

```bash
greenlight-recordings delete-recordings <meetingID[,meetingID,...]>
```

Before deletion, every discovered format-specific `metadata.xml` must pass the `bbb-origin=greenlight` safety invariant. The command then invokes `bbb-record --delete` for each recording.

## Inspect one recording

```bash
greenlight-recordings inspect <recordID>
```

This includes the external/Greenlight meetingID, internal meetingID, reference metadata path, and all discovered recording formats.

## Create a Greenlight Room

Create a Room owned by the unique Greenlight SuperAdmin:

```bash
greenlight-recordings create-room "Historical recordings"
```

Create it for a specific existing Greenlight user:

```bash
greenlight-recordings create-room "Historical recordings" --user user@example.com
```

The command uses Greenlight's normal Rails `Room` model, so Greenlight generates the Room `meeting_id` and `friendly_id`. Before creating, it checks whether the same owner already has a Room with the same name; in that case it returns `EXISTS` instead of creating a duplicate. If more than one SuperAdmin exists, `--user` is required.

A Room inherits its owner's Greenlight provider. SuperAdmin accounts normally use provider `bn`. On installations that have direct `BIGBLUEBUTTON_*` credentials but no `LOADBALANCER_*` credentials, room-recording deletion commands automatically fall back to the direct `greenlight` BBB provider for the API operation. This does not change the Room owner or the user's provider.

## Delete a Room or only its recordings

Delete all recordings associated with one exact current Greenlight Room while preserving the Room itself:

```bash
greenlight-recordings delete-room-recordings "Exact Room Name"
```

Delete one exact Greenlight Room together with all recordings associated with it:

```bash
greenlight-recordings delete-room "Exact Room Name"
```

Both commands fail without changing anything when zero or multiple Rooms have the supplied exact name. Recording deletion uses Greenlight/BBB APIs and tolerates only BBB `notFound` responses for recordings that were already removed; any other BBB error aborts the operation.

## Reassignment

Move one recording to an existing destination Greenlight Room:

```bash
greenlight-recordings move-recording <recordID> --to <destination-meetingID>
```

Move all eligible recordings belonging to one or several historical meetingIDs:

```bash
greenlight-recordings move-room <source-meetingID[,source-meetingID,...]> --to <destination-meetingID>
```

Before a recording modification, every format-specific metadata file found for that recording must explicitly have `bbb-origin=greenlight`. If any format fails that invariant, the operation aborts.

The reassignment derives a new BigBlueButton internal recording ID as `SHA1(destination meetingID)-timestamp`, renames every discovered format directory, updates corresponding metadata, preserves the original timestamp portion, and preserves metadata ownership and file attributes.

After reassignment, Greenlight's normal `RecordingsSync` / Re-Sync can discover the recordings under the destination Room.

## Deleted recordings

`deleted` is intentionally included in discovery and normalization. A recording that remains under BigBlueButton's `deleted` state is not expected to become visible in normal Greenlight recording listings solely because its Room association was normalized. Restoring recording state is a separate concern and is not performed implicitly by this tool.
