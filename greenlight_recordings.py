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
    return {child.tag: (child.text or '').strip() for child in list(meta)}


def is_greenlight_metadata(path):
    tree = parse_xml(path)
    meta = meta_map(tree.getroot())
    return meta.get('bbb-origin', '').strip().lower() == 'greenlight'


def discover_recordings():
    """Discover only from <state>/presentation/<recordID>/metadata.xml, following symlinks."""
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
                internal_meeting_id = ''
                room_name = ''
                if meeting is not None:
                    ext_id = (meeting.attrib.get('externalId') or '').strip()
                    internal_meeting_id = (meeting.attrib.get('id') or '').strip()
                    room_name = (meeting.attrib.get('name') or '').strip()
                meta = meta_map(root)
                meeting_id = meta.get('meetingId', '').strip() or ext_id
                if not internal_meeting_id:
                    internal_meeting_id = record_id
                if not room_name:
                    room_name = (
                        meta.get('bbb-context-name', '').strip()
                        or meta.get('meeting-name', '').strip()
                        or meta.get('meetingName', '').strip()
                    )
                key = (state, record_id)
                if key in seen:
                    continue
                seen.add(key)
                yield {
                    'state': state,
                    'record_id': record_id,
                    'meeting_id': meeting_id,
                    'internal_meeting_id': internal_meeting_id,
                    'room_name': room_name,
                    'metadata': str(metadata),
                    'context_id': meta.get('bbb-context-id', '').strip(),
                }
            except SystemExit:
                raise
            except Exception as exc:
                print(f'WARN: skipping {metadata}: {exc}', file=sys.stderr)


def run_greenlight_rails(ruby, env=None):
    cmd = ['docker', 'exec']
    for key, value in (env or {}).items():
        cmd += ['-e', f'{key}={value}']
    cmd += [GREENLIGHT_CONTAINER, 'bundle', 'exec', 'rails', 'runner', ruby]
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError:
        fail('docker was not found; Greenlight operations require local Docker access')
    except subprocess.CalledProcessError as exc:
        fail(f'Greenlight Rails command failed in container {GREENLIGHT_CONTAINER}: {exc.output.strip()}')


def greenlight_rooms():
    ruby = "Room.order(:created_at).pluck(:meeting_id,:friendly_id,:name).each{|r| puts r.map{|v| v.to_s.gsub(\"\\t\",\" \")}.join(\"\\t\") }"
    out = run_greenlight_rails(ruby)
    rooms = {}
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) >= 3:
            meeting_id, friendly_id, name = parts[0], parts[1], '\t'.join(parts[2:])
            rooms[meeting_id] = {'meeting_id': meeting_id, 'friendly_id': friendly_id, 'name': name}
    return rooms


def room_provenance():
    detail = Counter()
    for r in discover_recordings():
        mid = r['meeting_id'] or '(missing)'
        name = r['room_name'] or '(unknown)'
        detail[(mid, name)] += 1

    total = defaultdict(int)
    first_known_name = {}
    for (mid, name), count in detail.items():
        total[mid] += count
        if mid not in first_known_name:
            first_known_name[mid] = name
        elif first_known_name[mid] == '(unknown)' and name != '(unknown)':
            first_known_name[mid] = name
    return detail, total, first_known_name


def cmd_rooms(args):
    detail, total, first_name = room_provenance()
    check_greenlight = args.check_greenlight or args.missing_only
    existing = greenlight_rooms() if check_greenlight else {}

    if not args.missing_only:
        print('DETAIL: recording counts by meetingID + observed room name')
        print('COUNT\tMEETING_ID\tROOM_NAME')
        for (mid, name), count in sorted(detail.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1])):
            print(f'{count}\t{mid}\t{name}')
        print('\nCONSOLIDATED: recording counts by meetingID')

    if check_greenlight:
        print('COUNT\tEXISTS\tMEETING_ID\tROOM_NAME')
    else:
        print('COUNT\tMEETING_ID\tROOM_NAME')

    for mid, count in sorted(total.items(), key=lambda kv: (-kv[1], kv[0])):
        if args.missing_only and (mid == '(missing)' or mid in existing):
            continue
        name = first_name.get(mid, '(unknown)')
        if check_greenlight:
            exists = 'yes' if mid in existing else 'no'
            print(f'{count}\t{exists}\t{mid}\t{name}')
        else:
            print(f'{count}\t{mid}\t{name}')


def cmd_orphans(args):
    rooms = greenlight_rooms()
    _, total, names = room_provenance()
    print('COUNT\tMEETING_ID\tROOM_NAME')
    for mid, count in sorted(total.items(), key=lambda kv: (-kv[1], kv[0])):
        if mid != '(missing)' and mid not in rooms:
            print(f'{count}\t{mid}\t{names.get(mid, "(unknown)")}')


def cmd_bbb_record_ids(args):
    sources = {x.strip() for x in args.meeting_ids.split(',') if x.strip()}
    if not sources:
        fail('No Greenlight meetingID supplied')
    matches = [r for r in discover_recordings() if r['meeting_id'] in sources]
    if not matches:
        fail('No eligible Greenlight recordings found for supplied meetingID(s)')
    matches.sort(key=lambda r: (r['meeting_id'], r['internal_meeting_id'], r['state']))
    print('STATE\tMEETING_ID\tROOM_NAME\tINTERNAL_MEETING_ID\tRECORD_ID\tBBB_RECORD_DELETE')
    for r in matches:
        internal_id = r['internal_meeting_id'] or r['record_id']
        print(
            f"{r['state']}\t{r['meeting_id']}\t{r['room_name'] or '(unknown)'}\t"
            f"{internal_id}\t{r['record_id']}\tbbb-record --delete {internal_id}"
        )


def cmd_create_room(args):
    ruby = r'''
name = ENV.fetch('GLRT_ROOM_NAME')
email = ENV['GLRT_USER_EMAIL'].to_s.strip
if email.empty?
  role = Role.find_by(name: 'SuperAdmin', provider: 'bn')
  abort('ERROR: SuperAdmin role not found') if role.nil?
  users = User.where(role_id: role.id).to_a
  abort("ERROR: expected exactly one SuperAdmin user, found #{users.length}; use --user EMAIL") unless users.length == 1
  user = users.first
else
  users = User.where('LOWER(email) = ?', email.downcase).to_a
  abort("ERROR: user not found: #{email}") if users.empty?
  abort("ERROR: multiple users found for email #{email}") unless users.length == 1
  user = users.first
end
existing = Room.where(user_id: user.id).where('LOWER(name) = ?', name.downcase).first
if existing
  puts ['EXISTS', existing.meeting_id, existing.friendly_id, user.email, existing.name].join("\t")
else
  room = Room.create!(name: name, user_id: user.id)
  puts ['CREATED', room.meeting_id, room.friendly_id, user.email, room.name].join("\t")
end
'''.strip()
    env = {'GLRT_ROOM_NAME': args.name}
    if args.user:
        env['GLRT_USER_EMAIL'] = args.user
    out = run_greenlight_rails(ruby, env=env)
    print('STATUS\tMEETING_ID\tFRIENDLY_ID\tOWNER_EMAIL\tROOM_NAME')
    print(out.strip())


def find_recording_formats(state, record_id):
    """Find every <state>/<format>/<recordID>/metadata.xml, following format symlinks."""
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
    room = greenlight_rooms().get(destination_meeting_id)
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
        def set_existing_meta(tag, value):
            node = meta.find(tag)
            if node is not None:
                node.text = value
        set_existing_meta('meetingId', destination_meeting_id)
        set_existing_meta('bbb-context-id', room.get('friendly_id', ''))
        if room.get('name'):
            set_existing_meta('bbb-context-name', room['name'])
            set_existing_meta('meeting-name', room['name'])
            set_existing_meta('meetingName', room['name'])


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
    for key, value in list(elem.attrib.items()):
        if old in value:
            elem.attrib[key] = value.replace(old, new)
    for child in list(elem):
        replace_text_recursive(child, old, new)


def move_one(record, destination_meeting_id, room=None):
    state = record['state']
    old_record_id = record['record_id']
    if '-' not in old_record_id:
        fail(f'Unexpected recordID format: {old_record_id}')
    timestamp = old_record_id.split('-', 1)[1]
    new_record_id = f'{sha1_hex(destination_meeting_id)}-{timestamp}'
    if new_record_id == old_record_id:
        fail(f'Recording already belongs to destination meetingID: {old_record_id}')

    room = room or validate_destination_room(destination_meeting_id)
    formats = find_recording_formats(state, old_record_id)
    if not formats:
        fail(f'No format metadata found for {old_record_id} in {state}')

    prepared = []
    for fmt, old_dir, metadata in formats:
        tree = parse_xml(metadata)
        origin = meta_map(tree.getroot()).get('bbb-origin', '').strip().lower()
        if origin != 'greenlight':
            fail(f'Safety invariant failed for {metadata}: bbb-origin is not greenlight')
        target_dir = old_dir.parent / new_record_id
        if target_dir.exists():
            fail(f'Target directory already exists: {target_dir}')
        original_bytes = metadata.read_bytes()
        original_stat = metadata.stat()
        update_metadata_tree(tree, new_record_id, destination_meeting_id, room)
        replace_text_recursive(tree.getroot(), old_record_id, new_record_id)
        prepared.append({
            'fmt': fmt,
            'old_dir': old_dir,
            'target_dir': target_dir,
            'tree': tree,
            'original_bytes': original_bytes,
            'mode': original_stat.st_mode,
            'uid': original_stat.st_uid,
            'gid': original_stat.st_gid,
        })

    renamed = []
    written = []
    try:
        for item in prepared:
            os.rename(item['old_dir'], item['target_dir'])
            renamed.append(item)
        for item in prepared:
            target_metadata = item['target_dir'] / 'metadata.xml'
            atomic_write_tree(item['tree'], target_metadata)
            written.append(item)
    except Exception as exc:
        rollback_errors = []
        for item in written:
            try:
                target_metadata = item['target_dir'] / 'metadata.xml'
                target_metadata.write_bytes(item['original_bytes'])
                os.chmod(target_metadata, item['mode'])
                os.chown(target_metadata, item['uid'], item['gid'])
            except Exception as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        for item in reversed(renamed):
            try:
                if item['target_dir'].exists() and not item['old_dir'].exists():
                    os.rename(item['target_dir'], item['old_dir'])
            except Exception as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        extra = f'; rollback errors: {rollback_errors}' if rollback_errors else ''
        fail(f'Move failed; rollback attempted: {exc}{extra}')

    print(f'MOVED\t{state}\t{old_record_id}\t{new_record_id}\t{destination_meeting_id}\tformats={len(formats)}')


def cmd_move_recording(args):
    matches = [r for r in discover_recordings() if r['record_id'] == args.record_id]
    if not matches:
        fail(f'Greenlight recording not found: {args.record_id}')
    if len(matches) > 1:
        fail(f'RecordID appears in multiple states: {args.record_id}')
    move_one(matches[0], args.to)


def cmd_move_room(args):
    sources = {x.strip() for x in args.source_meeting_ids.split(',') if x.strip()}
    if not sources:
        fail('No source meetingID supplied')
    if args.to in sources:
        fail('Destination meetingID must not also be a source meetingID')
    room = validate_destination_room(args.to)
    matches = [r for r in discover_recordings() if r['meeting_id'] in sources]
    if not matches:
        fail('No eligible Greenlight recordings found for supplied source meetingID(s)')
    print(f'Found {len(matches)} eligible recording(s).')
    for record in matches:
        move_one(record, args.to, room=room)


def cmd_inspect(args):
    for r in discover_recordings():
        if r['record_id'] == args.record_id:
            print(f"state: {r['state']}")
            print(f"recordID: {r['record_id']}")
            print(f"meetingID: {r['meeting_id']}")
            print(f"internal meetingID: {r['internal_meeting_id']}")
            print(f"room name: {r['room_name']}")
            print(f"bbb-context-id: {r['context_id']}")
            print(f"reference metadata: {r['metadata']}")
            formats = find_recording_formats(r['state'], r['record_id'])
            print('formats: ' + ', '.join(sorted(item[0] for item in formats)))
            return
    fail(f'Greenlight recording not found: {args.record_id}')


def build_parser():
    parser = argparse.ArgumentParser(prog='greenlight-recordings')
    sub = parser.add_subparsers(dest='command', required=True)

    cmd = sub.add_parser('rooms', help='report Greenlight recording provenance by historical meetingID')
    cmd.add_argument('--check-greenlight', action='store_true', help='mark whether each meetingID currently exists as a Greenlight Room')
    cmd.add_argument('--missing-only', action='store_true', help='show only meetingIDs that do not currently exist as Greenlight Rooms; implies --check-greenlight')
    cmd.set_defaults(func=cmd_rooms)

    cmd = sub.add_parser('orphans', help='list recording meetingIDs that no longer exist as Greenlight Rooms')
    cmd.set_defaults(func=cmd_orphans)

    cmd = sub.add_parser('bbb-record-ids', help='list internal meetingIDs usable with bbb-record --delete')
    cmd.add_argument('meeting_ids', help='one or more Greenlight/external meetingIDs separated by commas')
    cmd.set_defaults(func=cmd_bbb_record_ids)

    cmd = sub.add_parser('create-room', help='create a Greenlight Room for SuperAdmin or a specified user')
    cmd.add_argument('name', help='room name')
    cmd.add_argument('--user', metavar='EMAIL', help='room owner email; defaults to the unique SuperAdmin user')
    cmd.set_defaults(func=cmd_create_room)

    cmd = sub.add_parser('inspect', help='inspect one eligible Greenlight recording')
    cmd.add_argument('record_id')
    cmd.set_defaults(func=cmd_inspect)

    cmd = sub.add_parser('move-recording', help='reassign one recording to an existing Greenlight Room')
    cmd.add_argument('record_id')
    cmd.add_argument('--to', required=True, metavar='DESTINATION_MEETING_ID')
    cmd.set_defaults(func=cmd_move_recording)

    cmd = sub.add_parser('move-room', help='reassign all recordings from one or more historical meetingIDs')
    cmd.add_argument('source_meeting_ids', help='one or more source meetingIDs separated by commas')
    cmd.add_argument('--to', required=True, metavar='DESTINATION_MEETING_ID')
    cmd.set_defaults(func=cmd_move_room)

    return parser


def main():
    if os.geteuid() != 0:
        fail('This CLI must run as root because BBB recording trees may require privileged access')
    args = build_parser().parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
