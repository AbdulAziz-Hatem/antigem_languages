#!/usr/bin/env python3
"""
Decides whether an update to a pack can be published without a maintainer: its sender must be one of the pack's
authors (their GitHub username in "authors" of the pack on main), and the update must keep the pack's identity and
its people.

    python3 tools/author_update.py <pack on main> <updated pack> <github username>

Prints the decision, writes author=true|false and ok=true|false to $GITHUB_OUTPUT, and the reasons a maintainer has
to look at it to $RUNNER_TEMP/author-reasons.md. Exit code 0 either way (2 when a file can't be read).
The files are only read as JSON data. Needs Python 3.8+, nothing else.
"""
import json
import os
import sys
import tempfile
from collections import OrderedDict

# An update that loses more than this share of the pack's translated texts needs a maintainer.
MAX_LOSS = 0.2


def github_names(pack):
    out = set()
    for a in pack.get('authors') or []:
        if isinstance(a, dict) and isinstance(a.get('github'), str) and a['github'].strip():
            out.add(a['github'].strip().lstrip('@').lower())
    return out


def is_author(pack, login):
    return bool(login) and login.strip().lstrip('@').lower() in github_names(pack)


def same_author(a, b):
    if a.get('github') and b.get('github'):
        return str(a['github']).strip().lstrip('@').lower() == str(b['github']).strip().lstrip('@').lower()
    return str(a.get('name', '')).strip().lower() == str(b.get('name', '')).strip().lower()


def translated(pack):
    strings = pack.get('strings')
    if not isinstance(strings, dict):
        return 0
    return sum(1 for v in strings.values() if (v if isinstance(v, str) else isinstance(v, dict) and v.get('other')))


def review_reasons(old, new, login, whole_file=True):
    """Why a maintainer has to review this update (empty: the author can publish it).
    whole_file: the update replaces the pack (a pull request), so texts can disappear; the form merges instead."""
    reasons = []
    if not is_author(old, login):
        reasons.append('@%s isn\'t one of the GitHub usernames in this pack\'s `authors`' % login)
    if (old.get('code') or '') != (new.get('code') or ''):
        reasons.append('it changes the language code (`%s` → `%s`)' % (old.get('code'), new.get('code')))
    old_authors = [a for a in old.get('authors') or [] if isinstance(a, dict)]
    new_authors = [a for a in new.get('authors') or [] if isinstance(a, dict)]
    gone = [a.get('name') or a.get('github') for a in old_authors if not any(same_author(a, b) for b in new_authors)]
    if gone:
        reasons.append('it removes %s from `authors`' % ', '.join('**%s**' % g for g in gone))
    added = sorted(github_names(new) - github_names(old))
    if added:
        reasons.append('it adds %s to `authors`: people listed there can publish updates to this pack, so a '
                       'maintainer approves new ones once' % ', '.join('@' + g for g in added))
    if whole_file:
        before, after = translated(old), translated(new)
        if before and after < before * (1 - MAX_LOSS):
            reasons.append('it removes %d of the pack\'s %d translated texts' % (before - after, before))
    return reasons


def load(path):
    with open(path, encoding='utf-8-sig') as f:
        o = json.load(f, object_pairs_hook=OrderedDict)
    if not isinstance(o, dict):
        raise ValueError('not a JSON object')
    return o


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    try:
        old, new = load(argv[0]), load(argv[1])
    except (OSError, ValueError) as e:
        print('can\'t read the packs: %s' % e, file=sys.stderr)
        return 2
    login = argv[2]
    author = is_author(old, login)
    reasons = review_reasons(old, new, login) if author else []
    out = os.environ.get('GITHUB_OUTPUT')
    if out:
        with open(out, 'a', encoding='utf-8') as f:
            f.write('author=%s\nok=%s\n' % (str(author).lower(), str(author and not reasons).lower()))
    with open(os.path.join(os.environ.get('RUNNER_TEMP') or tempfile.gettempdir(), 'author-reasons.md'), 'w',
              encoding='utf-8', newline='\n') as f:
        f.write(''.join('- %s\n' % r for r in reasons))
    if not author:
        print('@%s is not an author of this pack: a maintainer reviews it' % login)
    elif reasons:
        print('@%s is an author, but a maintainer has to look at it:\n%s' % (login, '\n'.join('  ' + r for r in reasons)))
    else:
        print('@%s is an author and the update keeps the pack\'s code and people: it can be published' % login)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
