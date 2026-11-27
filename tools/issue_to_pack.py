#!/usr/bin/env python3
"""
Turns a "Share a translation" issue into a language pack file, for the share-translation workflow.

    ISSUE_BODY=… ISSUE_NUMBER=12 ISSUE_AUTHOR=octocat [MODE=auto] python3 tools/issue_to_pack.py

Two modes (the "action" output says what to do next):
    label (default)  a maintainer added the create-pr label: make the pack for a pull request  → action=pr
    auto             the issue was opened or edited: if the person who opened it is an author of the pack it
                     updates (tools/author_update.py), make the merged pack to publish right away → action=publish,
                     or explain why a maintainer has to look at it → action=review; else nothing → action=none

It reads the issue form, downloads the attached file (a .json, or a .zip with one .json in it) from GitHub's
attachment storage, and writes it to languages/<code>/<id>.json. An improvement of a pack that's already here is
merged into that pack: its texts replace the old ones, the other texts and every author stay.

The issue text is untrusted: the file is only downloaded from github.com attachment links, its size is limited,
and the path it's written to is made only from a checked language code and pack id.

It writes, in $RUNNER_TEMP (or the system's temp folder):
    pack-commit.txt   the commit message
    pack-pr.md        the pull request's description
    pack-comment.md   on failure, what to tell the person on the issue
and the step outputs action, path, id, title (to $GITHUB_OUTPUT). Exit code 1 when the issue can't be turned into a pack.
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
import author_update  # noqa: E402
from author_update import same_author  # noqa: E402

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


def find_target(o, kind, packs):
    """The id of the pack already here that this file updates, or None. A pack of the same language and variant
    counts when the file names it (its id or based_on) or has its id; for an improvement, the only pack of that
    language (and variant) is enough."""
    code = o['code']
    variant = slug(o.get('variant') or '')
    same = OrderedDict((i, p) for i, (_, p) in packs.items() if p.get('code') == code)
    same_variant = [i for i, p in same.items() if slug(p.get('variant') or '') == variant]
    if isinstance(o.get('id'), str) and o['id'] in same:
        return o['id']
    for c in (code.lower() + ('-' + variant if variant else ''), o.get('based_on')):
        if isinstance(c, str) and c in same_variant:
            return c
    if kind == 'improvement':
        if isinstance(o.get('based_on'), str) and o['based_on'] in same:
            return o['based_on']
        if len(same_variant) == 1:
            return same_variant[0]
        if len(same) == 1:
            return next(iter(same))
    return None


def place(o, kind, packs):
    """→ (path, pack id, the pack to write, the existing pack it improves or None)."""
    code = o['code']
    if kind == 'improvement':
        target = find_target(o, kind, packs)
        if target is None:
            raise Refused('I couldn\'t tell which pack this improves (there\'s no pack with the code `%s` here, or '
                          'several). If it\'s a new language, choose *A new language* in the form.' % code)
        path, old = packs[target]
        return path, target, merge_into(old, o), target

    # the id: the file's own "id", else the code (plus the variant: ne-formal). Not the file name: the app saves
    # files as antigem-language-<name>.json, which isn't a good id.
    variant = slug(o.get('variant') or '')
    derived = code.lower() + ('-' + variant if variant else '')
    pack_id = next((c for c in (o.get('id'), derived, code.lower())
                    if isinstance(c, str) and PACK_ID.match(c) and len(c) <= 60), None)
    if pack_id is None:
        raise Refused('The pack needs an id: lowercase letters, digits and hyphens, like `ne` or `hi-latn`.')
    clash = [i for i, (_, p) in packs.items() if p.get('code') == code and slug(p.get('variant') or '') == variant]
    if pack_id in packs and not clash and derived not in packs and PACK_ID.match(derived):
        pack_id = derived
    if pack_id in packs or clash:
        other = pack_id if pack_id in packs else clash[0]
        raise Refused('There\'s already a pack for this language here: `%s`. If this improves it, choose '
                      '*Improvements to a pack that\'s already here* in the form; if it\'s another style, give it a '
                      'variant (in the app: **⋮ → Name & details**) and send it again.' % packs[other][0])
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


def run(body, number, author, mode):
    sections = form_sections(body)
    if mode == 'auto' and not ({'The file', 'Is it a new pack or an improvement?'} <= set(sections)):
        print('not a "Share a translation" issue')
        output(action='none')
        return
    kind = kind_of(section(sections, 'Is it a new pack'))
    urls = attachment_urls(sections, body)
    if not urls:
        raise Refused('I couldn\'t find the attached file. Edit the issue and drag the language file (or its zip) '
                      'into **The file**.')
    if len(urls) > 1:
        raise Refused('There are %d attached files: please attach only the language file.' % len(urls))
    name = os.path.basename(urllib.parse.urlparse(urls[0]).path)
    _, data = json_from_attachment(name, download(urls[0]))
    o = parse_pack(data)
    packs = existing_packs()
    people = form_authors(section(sections, 'Who made it'))

    if mode == 'auto':
        # published right away only when the person who opened the issue is an author of the pack it updates
        target = find_target(o, kind, packs)
        if target is None or not author_update.is_author(packs[target][1], author):
            print('@%s isn\'t an author of a pack this updates: a maintainer adds the create-pr label' % author)
            output(action='none')
            return
        path, old = packs[target]
        pack = merge_into(old, o)
        pack['authors'] = add_authors(pack['authors'], people)
        reasons = author_update.review_reasons(old, pack, author, whole_file=False)
        if reasons:
            write('pack-comment.md', 'Thanks! You\'re one of the authors of `%s`, but this update needs a '
                                     'maintainer\'s review first because:\n\n%s\nA maintainer will look at it soon.\n'
                  % (path, ''.join('- %s\n' % r for r in reasons)))
            output(action='review')
            return
        pack_id, improves = target, target
    else:
        path, pack_id, pack, improves = place(o, kind, packs)
        authors = add_authors(list(pack.get('authors') or []), people)
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
    if mode == 'auto':
        write('pack-commit.txt', '%s\n\nPublished by its author @%s from the "Share a translation" issue #%s.\n'
              % (title, author, number))
        output(action='publish', path=path, id=pack_id, title=title)
        return

    credits = '\n'.join('- %s%s%s' % (a['name'], ' (@%s)' % a['github'] if a.get('github') else '',
                                       ', ' + a['role'] if a.get('role') else '') for a in pack['authors'])
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
    output(action='pr', path=path, id=pack_id, title=title)


def main():
    mode = 'auto' if os.environ.get('MODE') == 'auto' else 'label'
    try:
        run(os.environ.get('ISSUE_BODY', ''), os.environ.get('ISSUE_NUMBER', '?'), os.environ.get('ISSUE_AUTHOR', ''),
            mode)
        return 0
    except Refused as e:
        message = str(e)
    except Exception as e:  # a download that failed, a bug…
        message = 'Something went wrong while reading the file (%s: %s). A maintainer will look at it.' % (
            type(e).__name__, e)
    first = 'There\'s a problem with the attached file.' if mode == 'auto' else \
        'I couldn\'t turn this into a pull request yet.'
    write('pack-comment.md', '%s\n\n%s\n' % (first, message))
    print(message, file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
