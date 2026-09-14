#!/usr/bin/env python3
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

BBB_ROOT = Path('/var/bigbluebutton')
STATES = ('published', 'unpublished', 'deleted')
DISCOVERY_FORMAT = 'presentation'
GREENLIGHT_CONTAINER = os.environ.get('GREENLIGHT_CONTAINER', 'greenlight-v3')


def fail(msg, code=1):
    print(f'ERROR: {msg}', file=sys.stderr)
    raise SystemExit(code)


def sha1_hex(text):
    return hashlib.sha1(text.encode('utf-8')).hexdigest()


def parse_xml(path):
    try:
        return ET.parse(path)
    except Exception as exc:
        fail(f'Cannot parse XML {path}: {exc}')


def get_text(root, xpath):
    node = root.find(xpath)
    return (node.text or '').strip() if node is not None and node.text else ''


def meta_map(root):
    meta = root.find('meta')
    if meta is None:
        return {}
    result = {}
    for child in list(meta):
        result[child.tag] = (child.text or '').strip()
    return result


def is_greenlight_metadata(path):
    tree = parse_xml(path)
    meta = meta_map(tree.getroot())
    return meta.get('bbb-origin', '').strip().lower() == 'greenlight'


def discover_recordings():
    seen = set()
    for state in STATES:
        base = BBB_ROOT / state / DISCOVERY_FORMAT
        if not base.exists():
            continue
        for entry in os.scandir(base):
            try:
                if not entry.is_dir(follow_symlinks=True):
                    continue
            except OSError:
                continue
            metadata = Path(entry.path) / 'metadata.xml'
            if not metadata.exists():
                continue
            try:
                if not is_greenlight_metadata(metadata):
                    continue
                tree = parse_xml(metadata)
                root = tree.getroot()
                record_id = get_text(root, 'id') or Path(entry.path).name
                meeting = root.find('meeting')
                ext_id = ''
                room_name = ''
                if meeting is not None:
                    ext_id = (meeting.attrib.get('externalId') or '').strip()
                    room_name = (meeting.attrib.get('name') or '').strip()
                meta = meta_map(root)
                meeting_id = meta.get('meetingId', '').strip() or ext_id
                if not room_name:
                    room_name = meta.get('bbb-context-name', '').strip() or meta.get('meeting-name', '').strip() or meta.get('meetingName', '').strip()
                key = (state, record_id)
                if key in seen:
                    continue
                seen.add(key)
                yield {
                    'state': state,
                    'record_id': record_id,
                    'meeting_id': meeting_id,
                    'room_name': room_name,
                    'metadata': str(metadata),
                    'context_id': meta.get('bbb-context-id', '').strip(),
                }
            except SystemExit:
                raise
            except Exception as exc:
                print(f'WARN: skipping {metadata}: {exc}', file=sys.stderr)


def greenlight_rooms():
    ruby = "Room.order(:created_at).pluck(:meeting_id,:friendly_id,:name).each{|r| puts r.map{|v| v.to_s.gsub(\"\\t\",\" \")}.join(\"\\t\") }"
    cmd = ['docker', 'exec', GREENLIGHT_CONTAINER, 'bundle', 'exec', 'rails', 'runner', ruby]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as exc:
        fail(f'Cannot query Greenlight rooms using container {GREENLIGHT_CONTAINER}: {exc.output.strip()}')
    rooms = {}
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) >= 3:
            meeting_id, friendly_id, name = parts[0], parts[1], '\t'.join(parts[2:])
            rooms[meeting_id] = {'meeting_id': meeting_id, 'friendly_id': friendly_id, 'name': name}
    return rooms


def cmd_rooms(args):
    recs = list(discover_recordings())
    grouped = Counter()
    names = defaultdict(list)
    for r in recs:
        mid = r['meeting_id'] or '(missing)'
        name = r['room_name'] or '(unknown)'
        grouped[(mid, name)] += 1
        names[mid].append((name, grouped[(mid, name)]))

    consolidated = []
    per_mid = defaultdict(int)
    first_name = {}
    for (mid, name), count in grouped.items():
        per_mid[mid] += count
        if mid not in first_name or first_name[mid] in ('', '(unknown)'):
            first_name[mid] = name

    existing = greenlight_rooms() if args.check_greenlight else {}
    for mid, count in per_mid.items():
        consolidated.append((count, mid, first_name.get(mid, '(unknown)'), mid in existing if args.check_greenlight else None))
    consolidated.sort(key=lambda x: (-x[0], x[1]))

    if args.check_greenlight:
        print('COUNT\tEXISTS\tMEETING_ID\tROOM_NAME')
        for count, mid, name, exists in consolidated:
            print(f'{count}\t{\"yes\" if exists else \"no\"}\t{mid}\t{name}')
    else:
        print('COUNT\tMEETING_ID\tROOM_NAME')
        for count, mid, name, _ in consolidated:
            print(f'{count}\t{mid}\t{name}')


def cmd_orphans(args):
    rooms = greenlight_rooms()
    counts = Counter()
    names = {}
    for r in discover_recordings():
        mid = r['meeting_id']
        if mid and mid not in rooms:
            counts[mid] += 1
            if mid not in names or not names[mid]:
                names[mid] = r['room_name']
    print('COUNT\tMEETING_ID\tROOM_NAME')
    for mid, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f'{count}\t{mid}\t{names.get(mid, \"\")}')


def find_recording_formats(state, record_id):
    base = BBB_ROOT / state
    found = []
    if not base.exists():
        return found
    for fmt_entry in os.scandir(base):
        try:
            if not fmt_entry.is_dir(follow_symlinks=True):
                continue
        except OSError:
            continue
        candidate_dir = Path(fmt_entry.path) / record_id
        metadata = candidate_dir / 'metadata.xml'
        if metadata.exists():
            found.append((fmt_entry.name, candidate_dir, metadata))
    return found


def validate_destination_room(destination_meeting_id):
    rooms = greenlight_rooms()
    room = rooms.get(destination_meeting_id)
    if not room:
        fail(f'Destination meetingID is not an existing Greenlight room: {destination_meeting_id}')
    return room


def update_metadata_tree(tree, new_record_id, destination_meeting_id, room):
    root = tree.getroot()
    id_node = root.find('id')
    if id_node is not None:
        id_node.text = new_record_id
    meeting = root.find('meeting')
    if meeting is not None:
        meeting.set('id', new_record_id)
        meeting.set('externalId', destination_meeting_id)
        if room.get('name'):
            meeting.set('name', room['name'])
    meta = root.find('meta')
    if meta is not None:
        def set_meta(tag, value):
            node = meta.find(tag)
            if node is not None:
                node.text = value
        set_meta('meetingId', destination_meeting_id)
        set_meta('bbb-context-id', room.get('friendly_id', ''))
        if room.get('name'):
            set_meta('bbb-context-name', room['name'])
            set_meta('meeting-name', room['name'])
            set_meta('meetingName', room['name'])
    # Update playback URLs and any other textual references to the old recording ID later in caller.


def atomic_write_tree(tree, path):
    parent = path.parent
    fd, tmp_name = tempfile.mkstemp(prefix='.metadata.', suffix='.xml', dir=str(parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tree.write(tmp, encoding='utf-8', xml_declaration=True)
        shutil.copystat(path, tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def replace_text_recursive(elem, old, new):
    if elem.text and old in elem.text:
        elem.text = elem.text.replace(old, new)
    if elem.tail and old in elem.tail:
        elem.tail = elem.tail.replace(old, new)
    for k, v in list(elem.attrib.items()):
        if old in v:
            elem.attrib[k] = v.replace(old, new)
    for child in list(elem):
        replace_text_recursive(child, old, new)


def move_one(record, destination_meeting_id):
    state = record['state']
    old_record_id = record['record_id']
    if '-' not in old_record_id:
        fail(f'Unexpected recordID format: {old_record_id}')
    timestamp = old_record_id.split('-', 1)[1]
    new_record_id = f'{sha1_hex(destination_meeting_id)}-{timestamp}'
    if new_record_id == old_record_id:
        fail(f'Recording already belongs to destination meetingID: {old_record_id}')

    room = validate_destination_room(destination_meeting_id)
    formats = find_recording_formats(state, old_record_id)
    if not formats:
        fail(f'No format metadata found for {old_record_id} in {state}')

    prepared = []
    for fmt, old_dir, metadata in formats:
        tree = parse_xml(metadata)
        if meta_map(tree.getroot()).get('bbb-origin', '').strip().lower() != 'greenlight':
            fail(f'Safety invariant failed for {metadata}: bbb-origin is not greenlight')
        target_dir = old_dir.parent / new_record_id
        if target_dir.exists():
            fail(f'Target directory already exists: {target_dir}')
        update_metadata_tree(tree, new_record_id, destination_meeting_id, room)
        replace_text_recursive(tree.getroot(), old_record_id, new_record_id)
        prepared.append((fmt, old_dir, target_dir, metadata, tree))

    # Rename directories first; if any rename fails, roll back completed renames.
    renamed = []
    try:
        for fmt, old_dir, target_dir, metadata, tree in prepared:
            os.rename(old_dir, target_dir)
            renamed.append((target_dir, old_dir))
        for fmt, old_dir, target_dir, metadata, tree in prepared:
            atomic_write_tree(tree, target_dir / 'metadata.xml')
    except Exception as exc:
        for target_dir, old_dir in reversed(renamed):
            try:
                if target_dir.exists() and not old_dir.exists():
                    os.rename(target_dir, old_dir)
            except Exception:
                pass
        fail(f'Move failed and rollback was attempted: {exc}')

    print(f'MOVED\t{state}\t{old_record_id}\t{new_record_id}\t{destination_meeting_id}\tformats={len(formats)}')


def cmd_move_recording(args):
    matches = [r for r in discover_recordings() if r['record_id'] == args.record_id]
    if not matches:
        fail(f'Greenlight recording not found: {args.record_id}')
    if len(matches) > 1:
        fail(f'RecordID appears in multiple states; use a unique recording/state before moving: {args.record_id}')
    move_one(matches[0], args.to)


def cmd_move_room(args):
    sources = {x.strip() for x in args.source_meeting_ids.split(',') if x.strip()}
    if not sources:
        fail('No source meetingID supplied')
    validate_destination_room(args.to)
    matches = [r for r in discover_recordings() if r['meeting_id'] in sources]
    if not matches:
        fail('No eligible Greenlight recordings found for supplied source meetingID(s)')
    print(f'Found {len(matches)} eligible recording(s).')
    for r in matches:
        move_one(r, args.to)


def cmd_inspect(args):
    for r in discover_recordings():
        if r['record_id'] == args.record_id:
            print(f"state: {r['state']}")
            print(f"recordID: {r['record_id']}")
            print(f"meetingID: {r['meeting_id']}")
            print(f"room name: {r['room_name']}")
            print(f"bbb-context-id: {r['context_id']}")
            print(f"reference metadata: {r['metadata']}")
            fmts = find_recording_formats(r['state'], r['record_id'])
            print('formats: ' + ', '.join(sorted(f[0] for f in fmts)))
            return
    fail(f'Greenlight recording not found: {args.record_id}')


def build_parser():
    p = argparse.ArgumentParser(prog='greenlight-recordings')
    sub = p.add_subparsers(dest='command', required=True)

    q = sub.add_parser('rooms', help='report recording provenance grouped by historical meetingID')
    q.add_argument('--check-greenlight', action='store_true', help='mark whether each meetingID currently exists as a Greenlight Room')
    q.set_defaults(func=cmd_rooms)

    q = sub.add_parser('orphans', help='list recording meetingIDs that do not exist in Greenlight')
    q.set_defaults(func=cmd_orphans)

    q = sub.add_parser('inspect', help='inspect one eligible Greenlight recording')
    q.add_argument('record_id')
    q.set_defaults(func=cmd_inspect)

    q = sub.add_parser('move-recording', help='reassign one recording to an existing Greenlight Room')
    q.add_argument('record_id')
    q.add_argument('--to', required=True, metavar='DESTINATION_MEETING_ID')
    q.set_defaults(func=cmd_move_recording)

    q = sub.add_parser('move-room', help='reassign all recordings from one or more historical meetingIDs')
    q.add_argument('source_meeting_ids', help='one or more source meetingIDs separated by commas')
    q.add_argument('--to', required=True, metavar='DESTINATION_MEETING_ID')
    q.set_defaults(func=cmd_move_room)

    return p


def main():
    if os.geteuid() != 0:
        fail('This CLI must run as root because BBB recording trees may require privileged access')
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
