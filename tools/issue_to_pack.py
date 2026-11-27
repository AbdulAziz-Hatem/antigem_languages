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
attachment storage, and works out from the file where it goes: its "code" is the folder, and a pack here with the
same code and variant (or the file's own "id") is the pack it updates. An update is merged into that pack: its
texts replace the old ones, the other texts and every author stay. Otherwise it's a new languages/<code>/<id>.json.

The issue text is untrusted: the file is only downloaded from github.com attachment links, its size is limited,
and the path it's written to is made only from a checked language code and pack id.

It writes, in $RUNNER_TEMP (or the system's temp folder):
    pack-commit.txt   the commit message
    pack-pr.md        the pull request's description
    pack-comment.md   on failure, what to tell the person on the issue
and the step outputs action, path, id, title (to $GITHUB_OUTPUT). Exit code 1 when the issue can't be turned into a pack.
Needs Python 3.8+, nothing else.
"""
import hashlib
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
from build_index import CODE, GITHUB, LANGUAGES, PACK_ID, ROOT, REPOSITORY, pack_files, variant_key  # noqa: E402
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


def form_authors(text):
    """'Name, @github, role' lines → authors. @github and role are optional; a GitHub username starts with @."""
    out = []
    for line in text.split('\n'):
        parts = [p.strip() for p in line.strip().lstrip('-*').split(',') if p.strip()]
        if not parts:
            continue
        if len(parts[0]) > 60 or len(parts[0].split()) > 5:
            continue  # a sentence about the translation, not a name: the file's own authors are used
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
    # some tools write authors as plain names: ["Sita"] → [{"name": "Sita"}]
    if isinstance(o.get('authors'), list):
        o['authors'] = [OrderedDict([('name', a.strip()[:100])]) if isinstance(a, str) else a
                        for a in o['authors'] if not (isinstance(a, str) and not a.strip())]
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


def find_target(o, packs):
    """The id of the pack already here that this file updates, or None for a new pack.
    The file says which: the same language code and variant (one pack per pair, the check makes sure), or its own
    "id" when that's a pack of the same language (so renaming the variant still updates it)."""
    code = o['code']
    same = OrderedDict((i, p) for i, (_, p) in packs.items() if p.get('code') == code)
    if isinstance(o.get('id'), str) and o['id'] in same:
        return o['id']
    key = variant_key(o.get('variant'))
    return next((i for i, p in same.items() if variant_key(p.get('variant')) == key), None)


def new_pack_id(o, packs):
    """The file's own "id", else the code plus the variant: ne, ne-formal. Not the file name: the app saves files
    as antigem-language-<name>.json."""
    code, variant = o['code'].lower(), (o.get('variant') or '').strip()
    suffix = slug(variant) or (hashlib.sha1(variant.encode('utf-8')).hexdigest()[:6] if variant else '')
    for c in (o.get('id'), code + ('-' + suffix if suffix else '')):
        if isinstance(c, str) and PACK_ID.match(c) and len(c) <= 60 and c not in packs:
            return c
    raise Refused('I couldn\'t find a free name for this pack. Give it a variant in the app (**⋮ → Name & '
                  'details**) and send it again, or say in the issue what it should be called.')


def place(o, packs):
    """→ (path, pack id, the pack to write, the existing pack it updates or None)."""
    target = find_target(o, packs)
    if target is not None:
        path, old = packs[target]
        return path, target, merge_into(old, o), target
    pack_id = new_pack_id(o, packs)
    if o.get('id') not in (None, '', pack_id):
        o['id'] = pack_id
    return 'languages/%s/%s.json' % (o['code'], pack_id), pack_id, o, None


# ---------------------------------------------------------------- what we tell the person

INFO_MARK = '<!-- share-translation:about-this-file -->'


def published_stats():
    """id → its entry in index.json (texts translated, authors…), for the packs that are published."""
    try:
        with open(os.path.join(ROOT, 'index.json'), encoding='utf-8') as f:
            return {p['id']: p for p in json.load(f).get('languages', [])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def plain(text, limit=80):
    """Text from a pack, safe in a comment: one line, short, no markdown, no @mentions."""
    text = ' '.join(str(text or '').split())[:limit]
    text = re.sub(r'([\\`*_\[\]<>|~#])', r'\\\1', text)
    return text.replace('@', '@\u200b')


def describe(name, native, variant):
    text = '**%s**' % plain(name)
    if native and native != name:
        text += ' · %s' % plain(native)
    return text + (' · %s' % plain(variant) if variant else '')


def people_of(pack):
    # profile links, not @mentions: listing a pack mustn't notify its authors
    names = ['[%s](https://github.com/%s)' % (plain(a['name'], 40), a['github']) if a.get('github') else plain(a['name'], 40)
             for a in pack.get('authors') or [] if isinstance(a, dict) and a.get('name')]
    return ', '.join(names[:4]) + (' +%d' % (len(names) - 4) if len(names) > 4 else '') or '—'


def about_file(o, packs, target):
    """Markdown: what this file is, which packs of its language are already here, and what will happen to it."""
    code = o['code']
    stats = published_stats()
    same = sorted(((i, path, p) for i, (path, p) in packs.items() if p.get('code') == code),
                  key=lambda x: (variant_key(x[2].get('variant')), x[0]))
    lines = [INFO_MARK, '### What happens with this file', '',
             'Your file is %s (language code `%s`%s).' % (
                 describe(o.get('name') or code, o.get('native_name'), ''), code,
                 ', variant *%s*' % plain(o['variant']) if (o.get('variant') or '').strip() else ', no variant'), '']
    if target is not None:
        path, p = packs[target]
        lines += ['🔄 **It updates a pack that\'s already here:** `%s` (%s).' % (
                      path, describe(p.get('name') or code, p.get('native_name'), p.get('variant'))),
                  'Your texts replace the ones there. Texts your file doesn\'t have stay as they are, and everyone '
                  'in its authors stays.', '']
    else:
        try:
            new_path = 'languages/%s/%s.json' % (code, new_pack_id(o, packs))
        except Refused:
            new_path = 'languages/%s/…' % code
        lines += ['🆕 **It becomes a new pack:** `%s`.' % new_path, '']
    if same:
        lines += ['**%s of this language here:**' % ('The only pack' if len(same) == 1 else 'All %d packs' % len(same)),
                  '', '| Pack | Variant | Translated | By |', '|---|---|---|---|']
        for i, path, p in same:
            st = stats.get(i)
            done = '%d%%' % (100 * st['translated'] // max(1, st['total'])) if st else 'not published'
            mark = ' ← yours updates this' if i == target else ''
            lines.append('| [`%s`](https://github.com/%s/blob/main/%s)%s | %s | %s | %s |' % (
                os.path.basename(path), REPOSITORY, path, mark, plain(p.get('variant')) or '—', done, people_of(p)))
        lines.append('')
        if target is not None:
            lines.append('*Not what you meant?* To add **another style or script** instead of updating this pack, '
                         'give your language a variant that isn\'t in the list (in the app: **⋮ → Name & details**, '
                         'e.g. *Formal*), save the file again and attach it here by editing this issue.')
        else:
            lines.append('*Not what you meant?* To **update** one of these packs instead, give your language exactly '
                         'the same variant as that pack (in the app: **⋮ → Name & details**; no variant for the '
                         'one marked —), save the file again and attach it here by editing this issue.')
        lines.append('')
    else:
        lines += ['There\'s no pack of this language here yet: yours will be the first. '
                  'See [all languages](https://github.com/%s#languages).' % REPOSITORY, '']
    return '\n'.join(lines)


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
    if mode == 'auto' and not ({'The file', 'Who made it'} <= set(sections)):
        print('not a "Share a translation" issue')
        output(action='none')
        return
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

    action = 'pr'
    if mode == 'auto':
        # everyone is told what the file does and which packs of its language are here (pack-info.md), and the file
        # is checked; it's published right away only when the person who opened the issue is an owner of the pack
        # it updates (.github/pack-owners.json) and keeps its code and people
        target = find_target(o, packs)
        info = about_file(o, packs, target)
        path, pack_id, pack, improves = place(o, packs)
        pack['authors'] = add_authors(list(pack.get('authors') or []), people)
        if target is None or not author_update.is_owner(target, author):
            action = 'inform'
            write('pack-info.md', info + '\nA maintainer will look at it and make a pull request from it. If you '
                                         'change the file (edit this issue), this note follows.\n')
        else:
            reasons = author_update.review_reasons(target, packs[target][1], pack, author, whole_file=False)
            if reasons:
                action = 'review'
                write('pack-info.md', info + '\nYou\'re one of this pack\'s owners, but this update needs a '
                                             'maintainer\'s review first because:\n\n%s\nA maintainer will look at '
                                             'it soon.\n' % ''.join('- %s\n' % r for r in reasons))
            else:
                action = 'publish'
                write('pack-info.md', info + '\n✅ You\'re one of this pack\'s owners, so it\'s published right '
                                             'away.\n')
    else:
        path, pack_id, pack, improves = place(o, packs)
        authors = add_authors(list(pack.get('authors') or []), people)
        if author and not improves and not any(isinstance(a, dict) and a.get('github') for a in authors):
            # a new pack where nobody has a GitHub username: credit the person who sent it (the maintainer sees
            # this in the pull request before merging)
            authors.append(OrderedDict([('name', author), ('github', author)]))
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
        output(action=action, path=path, id=pack_id, title=title)
        return

    credits = '\n'.join('- %s%s%s' % (a['name'], ' (@%s)' % a['github'] if a.get('github') else '',
                                       ', ' + a['role'] if a.get('role') else '') for a in pack['authors'])
    notes = section(sections, 'Anything else')
    write('pack-commit.txt', '%s\n\nFrom the "Share a translation" issue #%s by @%s.\n' % (title, number, author))
    write('pack-pr.md', '\n'.join([
        'Made from the **Share a translation** issue #%s by @%s.' % (number, author), '',
        '**Language:** %s (`%s`)' % (lang, pack.get('code')),
        '**Pack:** `%s`, %s' % (path, 'an update: the new texts replace the old ones, the other texts and every '
                                      'author stay' if improves else 'a new pack'), '',
        '**Made by**', credits or '- (nobody listed)', '',
    ] + (['**Notes from the issue**', '', '> ' + notes.replace('\n', '\n> '), ''] if notes else []) + [
        '**Maintainer:** GitHub holds the check on pull requests opened by this bot: click **Approve workflows to '
        'run** (in the merge box) to start it. Before merging, have someone who speaks the language read it.', '',
        'Closes #%s' % number, '',
    ]))
    output(action='pr', path=path, id=pack_id, title=title,
           detected=('it updates `%s`' if improves else 'it adds the new pack `%s`') % path)


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
