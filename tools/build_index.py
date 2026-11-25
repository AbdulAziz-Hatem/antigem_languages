#!/usr/bin/env python3
"""
Checks every language pack and writes index.json (the list the AntiGem app reads) and the table in README.md.

    python3 tools/build_index.py            check all packs, then write index.json and README.md
    python3 tools/build_index.py --check    only check (exit code 1 when a pack has errors); used on pull requests
    python3 tools/build_index.py --check languages/ne/ne.json   check one file

A pack is languages/<code>/<id>.json. Errors stop it from being published; warnings are only shown.
Run it from anywhere: paths are relative to the repository root. Needs Python 3.8+, nothing else.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANGUAGES = os.path.join(ROOT, 'languages')
ENGLISH = os.path.join(ROOT, 'en.json')
INDEX = os.path.join(ROOT, 'index.json')
README = os.path.join(ROOT, 'README.md')
REPOSITORY = 'Santoshkurmi/antigem_languages'

FORMAT = 'antigem-language'
FORMAT_VERSIONS = (1, 2)
PLURAL_FORMS = ['zero', 'one', 'two', 'few', 'many', 'other']
PLACEHOLDER = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)\}')
PACK_ID = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
CODE = re.compile(r'^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$')
GITHUB = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$')
KNOWN_FIELDS = {'$schema', 'format','format_version', 'id', 'code', 'name', 'native_name', 'variant', 'rtl', 'description',
                'authors', 'based_on', 'app_version', 'strings', 'sources', 'author', 'about'}
AUTHOR_FIELDS = {'name', 'github', 'url', 'role'}
README_START = '<!-- languages:start -->'
README_END = '<!-- languages:end -->'


# ---------------------------------------------------------------- the same text hash as the app (LanguageManager.hashOf)

def java_hash(s):
    """Java's String.hashCode, as Integer.toHexString prints it."""
    h = 0
    b = s.encode('utf-16-be')
    for i in range(0, len(b), 2):
        h = (31 * h + ((b[i] << 8) | b[i + 1])) & 0xFFFFFFFF
    return format(h, 'x')


def text_hash(v):
    if isinstance(v, dict):
        return java_hash('\u0000'.join(str(v.get(f, '')) for f in PLURAL_FORMS))
    return java_hash(str(v))


def placeholders(v):
    texts = v.values() if isinstance(v, dict) else [v]
    return {m for t in texts for m in PLACEHOLDER.findall(str(t))}


# ---------------------------------------------------------------- reports

class Report:
    def __init__(self, path):
        self.path = path
        self.errors = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def print(self):
        rel = os.path.relpath(self.path, ROOT)
        gh = os.environ.get('GITHUB_ACTIONS') == 'true'
        for kind, items in (('error', self.errors), ('warning', self.warnings)):
            shown = items[:60]
            for m in shown:
                if gh:
                    print('::%s file=%s::%s' % (kind, rel, m.replace('\n', ' ')))
                else:
                    print('%s: %s: %s' % (rel, kind, m))
            if len(items) > len(shown):
                print('%s: %s: … and %d more' % (rel, kind, len(items) - len(shown)))


# ---------------------------------------------------------------- English

def load_english():
    with open(ENGLISH, encoding='utf-8') as f:
        en = json.load(f, object_pairs_hook=OrderedDict)
    strings = en.get('strings')
    if not isinstance(strings, dict) or not strings:
        sys.exit('en.json has no "strings"')
    return strings


# ---------------------------------------------------------------- one pack

def git(*args):
    try:
        return subprocess.run(['git', '-C', ROOT] + list(args), capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return ''


def check_pack(path, english, rep):
    """Checks one file; returns its index entry (or None when it can't be published)."""
    rel = os.path.relpath(path, ROOT).replace(os.sep, '/')
    parts = rel.split('/')
    if len(parts) != 3 or parts[0] != 'languages' or not parts[2].endswith('.json'):
        rep.error('a pack must be languages/<code>/<id>.json')
        return None
    folder, pack_id = parts[1], parts[2][:-5]
    if not PACK_ID.match(pack_id) or len(pack_id) > 60:
        rep.error('the file name "%s" is the pack id: use lowercase letters, digits and hyphens, like ne or pt-br-casual' % pack_id)

    raw = open(path, 'rb').read()
    if raw.startswith(b'\xef\xbb\xbf'):
        rep.error('the file starts with a byte order mark (BOM): save it as UTF-8 without BOM')
    if b'\r\n' in raw:
        rep.warn('the file has Windows line endings (CRLF); git stores it with LF')
    try:
        o = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=OrderedDict)
    except UnicodeDecodeError:
        rep.error('the file is not UTF-8')
        return None
    except ValueError as e:
        rep.error('not valid JSON: %s' % e)
        return None
    if not isinstance(o, dict):
        rep.error('the file must be a JSON object')
        return None

    if o.get('format') != FORMAT:
        rep.error('"format" must be "%s"' % FORMAT)
    if o.get('format_version', 1) not in FORMAT_VERSIONS:
        rep.error('"format_version" must be one of %s' % (FORMAT_VERSIONS,))
    for k in o:
        if k not in KNOWN_FIELDS:
            rep.warn('unknown field "%s" (ignored by the app)' % k)
    if 'author' in o or 'about' in o:
        rep.warn('"author" and "about" are the old fields: use "authors" and "description"')

    if o.get('id') not in (None, '', pack_id):
        rep.error('"id" is "%s" but the file is named %s.json: they must match' % (o.get('id'), pack_id))

    code = o.get('code')
    if not isinstance(code, str) or not CODE.match(code):
        rep.error('"code" must be a language code like ne, hi, pt-BR or zh-Hans')
        code = folder
    elif code != folder:
        rep.error('"code" is "%s" but the folder is languages/%s: put the file in languages/%s/' % (code, folder, code))
    if code.split('-')[0].lower() == 'en' and not o.get('variant'):
        rep.warn('an English pack should have a "variant" (like "Simple")')

    name = o.get('name')
    if not isinstance(name, str) or not name.strip():
        rep.error('"name" (the language\'s name in English, like Nepali) is required')
        name = pack_id
    native = o.get('native_name', '')
    if not isinstance(native, str):
        rep.error('"native_name" must be a string')
        native = ''
    elif not native.strip():
        rep.warn('"native_name" is empty: give the name in the language itself, like नेपाली')
    variant = o.get('variant', '')
    if not isinstance(variant, str) or len(variant) > 40:
        rep.error('"variant" must be a short text (40 characters at most)')
        variant = ''
    rtl = o.get('rtl', False)
    if not isinstance(rtl, bool):
        rep.error('"rtl" must be true or false')
        rtl = False
    description = o.get('description', '')
    if not isinstance(description, str) or len(description) > 600:
        rep.error('"description" must be a text of 600 characters at most')
        description = ''
    if not description.strip():
        rep.warn('"description" is empty: one or two lines about the translation help people pick it')
    based_on = o.get('based_on', '')
    if not isinstance(based_on, str):
        rep.error('"based_on" must be a pack id')
        based_on = ''

    authors = []
    raw_authors = o.get('authors')
    if raw_authors is None:
        rep.error('"authors" is required: a list like [{"name": "Your Name", "github": "your-username"}]')
        raw_authors = []
    if not isinstance(raw_authors, list):
        rep.error('"authors" must be a list')
        raw_authors = []
    for n, a in enumerate(raw_authors):
        if not isinstance(a, dict):
            rep.error('authors[%d] must be an object like {"name": "…"}' % n)
            continue
        for k in a:
            if k not in AUTHOR_FIELDS:
                rep.warn('authors[%d] has an unknown field "%s"' % (n, k))
        an = a.get('name')
        if not isinstance(an, str) or not an.strip():
            rep.error('authors[%d] needs a "name"' % n)
            continue
        entry = OrderedDict([('name', an.strip())])
        gh = a.get('github')
        if gh not in (None, ''):
            gh = str(gh).strip().lstrip('@')
            if not GITHUB.match(gh):
                rep.error('authors[%d].github "%s" is not a GitHub username' % (n, gh))
            else:
                entry['github'] = gh
        url = a.get('url')
        if url not in (None, ''):
            if not isinstance(url, str) or not url.startswith('https://'):
                rep.error('authors[%d].url must start with https://' % n)
            else:
                entry['url'] = url
        role = a.get('role')
        if role not in (None, ''):
            if not isinstance(role, str) or len(role) > 40:
                rep.error('authors[%d].role must be a short text' % n)
            else:
                entry['role'] = role.strip()
        authors.append(entry)
    if raw_authors and not authors:
        rep.error('"authors" has nobody with a name')
    if len(authors) > 50:
        rep.error('"authors" lists more than 50 people')

    strings = o.get('strings')
    if not isinstance(strings, dict):
        rep.error('"strings" (key → translated text) is required')
        strings = {}
    sources = o.get('sources', {})
    if not isinstance(sources, dict):
        rep.error('"sources" must be an object (key → hash of the English it was translated from)')
        sources = {}

    translated = outdated = 0
    unknown = []
    for key, v in strings.items():
        en = english.get(key)
        if en is None:
            unknown.append(key)
            continue
        if isinstance(en, dict):
            if not isinstance(v, dict):
                rep.error('%s: English has plural forms here, give {"one": …, "other": …}' % key)
                continue
            bad = [f for f in v if f not in PLURAL_FORMS]
            if bad:
                rep.error('%s: unknown plural form %s (forms: %s)' % (key, ', '.join(bad), ', '.join(PLURAL_FORMS)))
                continue
            if not v.get('other'):
                rep.error('%s: plural forms need "other"' % key)
                continue
            if not all(isinstance(t, str) for t in v.values()):
                rep.error('%s: every plural form must be a text' % key)
                continue
        else:
            if not isinstance(v, str):
                rep.error('%s: English is a plain text here, not %s' % (key, 'plural forms' if isinstance(v, dict) else type(v).__name__))
                continue
            if not v:
                rep.warn('%s: empty text (leave the key out to show English)' % key)
                continue
        extra = placeholders(v) - placeholders(en)
        if extra:
            have = placeholders(en)
            rep.error('%s: {%s} is not in the English text%s' % (
                key, '} {'.join(sorted(extra)), (' (English has {%s})' % '} {'.join(sorted(have))) if have else ' (English has none)'))
            continue
        missing = placeholders(en) - placeholders(v) - ({'count'} if isinstance(en, dict) else set())
        if missing:
            rep.warn('%s: leaves out {%s}' % (key, '} {'.join(sorted(missing))))
        texts = v.values() if isinstance(v, dict) else [v]
        if any(re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', t) for t in texts):
            rep.error('%s: has control characters' % key)
            continue
        translated += 1
        src = sources.get(key)
        if src and src != text_hash(en):
            outdated += 1
    if unknown:
        rep.warn('%d keys are not in today\'s English (from another app version?), e.g. %s' % (len(unknown), ', '.join(unknown[:5])))
    if translated == 0:
        rep.error('no translated texts')

    commit = git('log', '-1', '--format=%H', '--', rel)
    updated = git('log', '-1', '--format=%cs', '--', rel)
    return OrderedDict([
        ('id', pack_id),
        ('code', code),
        ('name', name.strip()),
        ('native_name', native.strip()),
        ('variant', variant.strip()),
        ('rtl', rtl),
        ('description', description.strip()),
        ('authors', authors),
        ('based_on', based_on.strip()),
        ('app_version', str(o.get('app_version', ''))),
        ('translated', translated),
        ('outdated', outdated),
        ('total', len(english)),
        ('path', rel),
        ('commit', commit),
        ('updated', updated),
        ('size', len(raw)),
        ('sha256', hashlib.sha256(raw).hexdigest()),
    ])


# ---------------------------------------------------------------- all packs

def pack_files():
    out = []
    if not os.path.isdir(LANGUAGES):
        return out
    for d in sorted(os.listdir(LANGUAGES)):
        p = os.path.join(LANGUAGES, d)
        if os.path.isdir(p):
            out += [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.endswith('.json')]
        elif d.endswith('.json'):
            out.append(p)
    return out


def readme_table(packs):
    if not packs:
        return 'No languages yet. Be the first: see [CONTRIBUTING.md](CONTRIBUTING.md).'
    rows = ['| Language | | Translated | By | File |', '|---|---|---|---|---|']
    for p in packs:
        lang = p['name'] + (' · ' + p['variant'] if p['variant'] else '')
        pct = 100 * p['translated'] // max(1, p['total'])
        by = ', '.join(('[%s](https://github.com/%s)' % (a['name'], a['github'])) if a.get('github') else a['name']
                       for a in p['authors'][:4]) + (' +%d' % (len(p['authors']) - 4) if len(p['authors']) > 4 else '')
        rows.append('| %s | %s | %d%% | %s | [%s](%s) |' % (
            lang, p['native_name'], pct, by, p['id'], p['path']))
    return '\n'.join(rows)


def write_if_changed(path, text):
    old = open(path, encoding='utf-8').read() if os.path.exists(path) else None
    if old != text:
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        return True
    return False


def main(argv):
    check_only = '--check' in argv
    files = [os.path.abspath(a) for a in argv if not a.startswith('--')] or pack_files()
    english = load_english()

    packs, failed, seen = [], 0, {}
    for path in files:
        rep = Report(path)
        entry = check_pack(path, english, rep)
        if entry is not None:
            other = seen.get(entry['id'])
            if other:
                rep.error('the id "%s" is taken by %s: pick another file name' % (entry['id'], other))
            seen[entry['id']] = entry['path']
        rep.print()
        if rep.errors or entry is None:
            failed += 1
        else:
            packs.append(entry)
        status = 'FAILED' if (rep.errors or entry is None) else 'ok'
        if entry is not None:
            print('%-40s %s  %d/%d texts, %d outdated, %d warnings' % (
                entry['path'], status, entry['translated'], entry['total'], entry['outdated'], len(rep.warnings)))
        else:
            print('%-40s %s' % (os.path.relpath(path, ROOT), status))

    packs.sort(key=lambda p: (p['name'].lower(), p['variant'].lower(), p['id']))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as f:
            f.write('### Language packs\n\n%d checked, %d with errors\n\n%s\n' % (len(files), failed, readme_table(packs)))

    if check_only:
        print('%d packs checked, %d with errors' % (len(files), failed))
        return 1 if failed else 0

    # publishing skips packs with errors (the pull request check stops them before they get here)
    index = OrderedDict([
        ('format', 'antigem-language-index'),
        ('format_version', 1),
        ('repository', REPOSITORY),
        ('english', OrderedDict([('texts', len(english)), ('sha256', hashlib.sha256(open(ENGLISH, 'rb').read()).hexdigest())])),
        ('languages', packs),
    ])
    changed = write_if_changed(INDEX, json.dumps(index, ensure_ascii=False, indent=2) + '\n')
    if os.path.exists(README):
        text = open(README, encoding='utf-8').read()
        a, b = text.find(README_START), text.find(README_END)
        if a >= 0 and b > a:
            text = text[:a + len(README_START)] + '\n' + readme_table(packs) + '\n' + text[b:]
            changed = write_if_changed(README, text) or changed
    print('index.json: %d languages%s' % (len(packs), ' (updated)' if changed else ''))
    if failed:
        print('%d packs with errors were left out of the index' % failed)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
