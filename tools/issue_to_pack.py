#!/usr/bin/env python3
"""
Turns a "Share a translation" issue into a language pack file, for the share-translation workflow.

    ISSUE_BODY=… ISSUE_NUMBER=12 ISSUE_AUTHOR=octocat python3 tools/issue_to_pack.py

It reads the issue form, downloads the attached file (a .json, or a .zip with one .json in it) from GitHub's
attachment storage, and writes it to languages/<code>/<id>.json. An improvement of a pack that's already here is
merged into that pack: its texts replace the old ones, the other texts and every author stay.

The issue text is untrusted: the file is only downloaded from github.com attachment links, its size is limited,
and the path it's written to is made only from a checked language code and pack id.

It writes, in $RUNNER_TEMP (or the system's temp folder):
    pack-commit.txt   the commit message
    pack-pr.md        the pull request's description
    pack-comment.md   on failure, what to tell the person on the issue
and the step outputs path, id, title (to $GITHUB_OUTPUT). Exit code 1 when the issue can't be turned into a pack.
Needs Python 3.8+, nothing else.
"""
import io
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_index import CODE, GITHUB, LANGUAGES, PACK_ID, ROOT, REPOSITORY, pack_files  # noqa: E402

MAX_BYTES = 20 * 1024 * 1024
TEMP = os.environ.get('RUNNER_TEMP') or tempfile.gettempdir()
ATTACHMENT = re.compile(
    r'https://github\.com/(?:user-attachments/files/\d+|%s/files/\d+)/[^\s)\]"\'<>]+' % re.escape(REPOSITORY))
NO_RESPONSE = '_No response_'


class Refused(Exception):
    """Something the person who opened the issue can fix: the message is posted on the issue."""


# ---------------------------------------------------------------- the issue form

def form_sections(body):
    """'### Heading\\n\\ntext' blocks of an issue form → {heading: text}."""
    out, current = OrderedDict(), None
    for line in body.replace('\r\n', '\n').split('\n'):
        m = re.match(r'^###\s+(.+?)\s*$', line)
        if m:
            current = m.group(1)
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return OrderedDict((k, '\n'.join(v).strip()) for k, v in out.items())


def section(sections, start):
    for k, v in sections.items():
        if k.lower().startswith(start.lower()):
            return '' if v == NO_RESPONSE else v
    return ''


def kind_of(text):
    t = text.lower()
    if t.startswith('improvement'):
        return 'improvement'
    if 'variant' in t:
        return 'variant'
    return 'new'


def form_authors(text):
    """'Name, @github, role' lines → authors. @github and role are optional; a GitHub username starts with @."""
    out = []
    for line in text.split('\n'):
        parts = [p.strip() for p in line.strip().lstrip('-*').split(',') if p.strip()]
        if not parts:
            continue
        a = OrderedDict([('name', parts[0].lstrip('@')[:100])])
        rest = []
        for p in parts:
            if 'github' not in a and p.startswith('@') and GITHUB.match(p[1:]):
                a['github'] = p[1:]
            elif p is not parts[0]:
                rest.append(p)
        if rest:
            role = ', '.join(rest)[:40].strip()
            a['role'] = role[:1].upper() + role[1:]
        out.append(a)
    return out


# ---------------------------------------------------------------- the file

def attachment_urls(sections, body):
    urls = ATTACHMENT.findall(section(sections, 'The file')) or ATTACHMENT.findall(body)
    seen = []
    for u in urls:
        u = u.rstrip('.,;')
        if u not in seen:
            seen.append(u)
    return seen


def download(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'antigem-languages-share-translation'})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise Refused('The attached file is bigger than %d MB.' % (MAX_BYTES // 1024 // 1024))
    return data


def json_from_attachment(name, data):
    """The bytes of the language file: the file itself, or the one .json inside a zip."""
    if name.lower().endswith('.zip') or data[:4] == b'PK\x03\x04':
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise Refused('The attached zip file can\'t be opened.')
        members = [i for i in z.infolist() if not i.is_dir() and i.filename.lower().endswith('.json')
                   and not i.filename.startswith('__MACOSX/') and not os.path.basename(i.filename).startswith('._')]
        if len(members) != 1:
            raise Refused('The zip file should have exactly one `.json` file in it (it has %d).' % len(members))
        if members[0].file_size > MAX_BYTES:
            raise Refused('The file in the zip is bigger than %d MB.' % (MAX_BYTES // 1024 // 1024))
        with z.open(members[0]) as f:
            data = f.read(MAX_BYTES + 1)
        name = os.path.basename(members[0].filename)
    return name, data


def parse_pack(data):
    if data.startswith(b'\xef\xbb\xbf'):
        data = data[3:]
    try:
        o = json.loads(data.decode('utf-8'), object_pairs_hook=OrderedDict)
    except UnicodeDecodeError:
        raise Refused('The file isn\'t UTF-8 text. Please send the file the app saved, as it is.')
    except ValueError as e:
        raise Refused('The file isn\'t valid JSON (%s). Please send the file the app saved, as it is.' % e)
    if not isinstance(o, dict) or o.get('format') != 'antigem-language':
        raise Refused('This isn\'t an AntiGem language file. In the app: **Settings → Language → ⋮ on your '
                      'language → Send to the community → Save the file**, then attach that file.')
    if not isinstance(o.get('code'), str) or not CODE.match(o['code']):
        raise Refused('The file\'s `"code"` should be a language code like `ne`, `pt-BR` or `zh-Hans`.')
    if not isinstance(o.get('strings'), dict) or not o['strings']:
        raise Refused('The file has no translated texts.')
    o.pop('notes', None)
    o.pop('$schema', None)
    return o


# ---------------------------------------------------------------- where it goes

def existing_packs():
    out = OrderedDict()
    for path in pack_files():
        rel = os.path.relpath(path, ROOT).replace(os.sep, '/')
        try:
            with open(path, encoding='utf-8-sig') as f:
                out[os.path.basename(path)[:-5]] = (rel, json.load(f, object_pairs_hook=OrderedDict))
        except (OSError, ValueError):
            pass
    return out


def slug(text):
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:30].strip('-')


def same_author(a, b):
    if a.get('github') and b.get('github'):
        return a['github'].lower() == b['github'].lower()
    return a.get('name', '').strip().lower() == b.get('name', '').strip().lower()


def add_authors(authors, new):
    for a in new:
        if isinstance(a, dict) and a.get('name') and not any(same_author(a, b) for b in authors if isinstance(b, dict)):
            authors.append(a)
    return authors


def merge_into(old, new):
    """An improvement: the old pack's header stays, the new texts replace the old ones, every author stays."""
    out = OrderedDict(old)
    for field in ('strings', 'sources'):
        merged = OrderedDict(old.get(field) or {})
        for k, v in (new.get(field) or {}).items():
            merged[k] = v
        out[field] = merged
    out['authors'] = add_authors(list(old.get('authors') or []), new.get('authors') or [])
    if new.get('app_version'):
        out['app_version'] = new['app_version']
    if not (old.get('description') or '').strip() and new.get('description'):
        out['description'] = new['description']
    out['format_version'] = max(int(old.get('format_version', 1) or 1), int(new.get('format_version', 1) or 1))
    return out


def place(o, kind, packs):
    """→ (path, pack id, the pack to write, the existing pack it improves or None)."""
    # the id: the file's own "id", else the code (plus the variant: ne-formal). Not the file name: the app saves
    # files as antigem-language-<name>.json, which isn't a good id.
    code = o['code']
    variant = slug(o.get('variant') or '')
    candidates = [o.get('id'), code.lower() + ('-' + variant if variant else ''), code.lower()]
    pack_id = next((c for c in candidates if isinstance(c, str) and PACK_ID.match(c) and len(c) <= 60), None)

    if kind == 'improvement':
        target = None
        for c in (o.get('based_on'), pack_id):
            if isinstance(c, str) and c in packs and packs[c][1].get('code') == code:
                target = c
                break
        if target is None:
            same_code = [i for i, (_, p) in packs.items() if p.get('code') == code]
            if len(same_code) == 1:
                target = same_code[0]
        if target is None:
            raise Refused('I couldn\'t tell which pack this improves (there\'s no pack with the code `%s` and the id '
                          '`%s` here). If it\'s a new language, choose *A new language* in the form.' % (code, pack_id))
        path, old = packs[target]
        return path, target, merge_into(old, o), target

    if pack_id is None:
        raise Refused('The pack needs an id: lowercase letters, digits and hyphens, like `ne` or `hi-latn`.')
    if pack_id in packs:
        alt = '%s-%s' % (pack_id, variant) if variant and not pack_id.endswith('-' + variant) else None
        if alt and alt not in packs and PACK_ID.match(alt):
            pack_id = alt
        else:
            raise Refused('There\'s already a pack with the id `%s` (`%s`). If this improves it, choose *Improvements '
                          'to a pack that\'s already here* in the form; if it\'s another style, give it a variant '
                          '(in the app: **⋮ → Name & details**) and send it again.' % (pack_id, packs[pack_id][0]))
    if o.get('id') not in (None, '', pack_id):
        o['id'] = pack_id
    return 'languages/%s/%s.json' % (code, pack_id), pack_id, o, None


# ---------------------------------------------------------------- main

def write(name, text):
    with open(os.path.join(TEMP, name), 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def output(**values):
    out = os.environ.get('GITHUB_OUTPUT')
    for k, v in values.items():
        v = str(v).replace('\n', ' ')
        if out:
            with open(out, 'a', encoding='utf-8') as f:
                f.write('%s=%s\n' % (k, v))
        print('%s=%s' % (k, v))


def run(body, number, author):
    sections = form_sections(body)
    kind = kind_of(section(sections, 'Is it a new pack'))
    urls = attachment_urls(sections, body)
    if not urls:
        raise Refused('I couldn\'t find the attached file. Edit the issue and drag the language file (or its zip) '
                      'into **The file**, then a maintainer will try again.')
    if len(urls) > 1:
        raise Refused('There are %d attached files: please attach only the language file.' % len(urls))
    name = os.path.basename(urllib.parse.urlparse(urls[0]).path)
    _, data = json_from_attachment(name, download(urls[0]))
    o = parse_pack(data)

    packs = existing_packs()
    path, pack_id, pack, improves = place(o, kind, packs)
    authors = add_authors(list(pack.get('authors') or []), form_authors(section(sections, 'Who made it')))
    if not authors and author:
        authors = [OrderedDict([('name', author), ('github', author)])]
    pack['authors'] = authors

    full = os.path.join(ROOT, *path.split('/'))
    if not os.path.realpath(full).startswith(os.path.realpath(LANGUAGES) + os.sep):
        raise Refused('The pack can\'t be written there.')
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(pack, ensure_ascii=False, indent=2) + '\n')

    lang = pack.get('name') or o.get('name') or pack_id
    if pack.get('variant'):
        lang += ' · ' + pack['variant']
    title = '%s %s (%s)' % ('Update' if improves else 'Add', lang, pack.get('code'))
    credits = '\n'.join('- %s%s%s' % (a['name'], ' (@%s)' % a['github'] if a.get('github') else '',
                                       ', ' + a['role'] if a.get('role') else '') for a in authors)
    notes = section(sections, 'Anything else')
    write('pack-commit.txt', '%s\n\nFrom the "Share a translation" issue #%s by @%s.\n' % (title, number, author))
    write('pack-pr.md', '\n'.join([
        'Made from the **Share a translation** issue #%s by @%s.' % (number, author), '',
        '**Language:** %s' % (section(sections, 'Language') or lang),
        '**Pack:** `%s`' % path,
        '**Kind:** %s' % ({'new': 'a new language', 'variant': 'a new variant',
                           'improvement': 'improvements, merged into `%s`' % improves}[kind]), '',
        '**Made by**', credits or '- (nobody listed)', '',
    ] + (['**Notes from the issue**', '', '> ' + notes.replace('\n', '\n> '), ''] if notes else []) + [
        'The check on this pull request looks at the file. Before merging, have someone who speaks the language '
        'read it.', '',
        'Closes #%s' % number, '',
    ]))
    output(path=path, id=pack_id, title=title)


def main():
    try:
        run(os.environ.get('ISSUE_BODY', ''), os.environ.get('ISSUE_NUMBER', '?'), os.environ.get('ISSUE_AUTHOR', ''))
        return 0
    except Refused as e:
        message = str(e)
    except Exception as e:  # a download that failed, a bug…
        message = 'Something went wrong while reading the file (%s: %s). A maintainer will look at it.' % (
            type(e).__name__, e)
    write('pack-comment.md', 'I couldn\'t turn this into a pull request yet.\n\n%s\n' % message)
    print(message, file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
