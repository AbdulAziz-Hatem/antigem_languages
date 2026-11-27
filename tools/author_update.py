#!/usr/bin/env python3
"""
Decides whether an update to a pack can be published without a maintainer.

Who: the pack's owners in .github/pack-owners.json (GitHub usernames). A pack's creators (its authors when it was
first published) are put there automatically; anyone else only by a maintainer editing that file. Being listed in
a pack's "authors" is credit, not a right to publish.

What: the update must keep the pack's language code, remove nobody from "authors" and (in a pull request, which
replaces the whole file) not drop more than a fifth of the pack's translated texts.

    python3 tools/author_update.py <pack on main> <updated pack> <github username>

Prints the decision, writes author=true|false (an owner?) and ok=true|false (publish it?) to $GITHUB_OUTPUT, and the
reasons a maintainer has to look at it to $RUNNER_TEMP/author-reasons.md. Exit code 0 either way (2 when a file
can't be read). The files are only read as JSON data. Needs Python 3.8+, nothing else.
"""
import json
import os
import sys
import tempfile
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWNERS = os.path.join(ROOT, '.github', 'pack-owners.json')

# An update that loses more than this share of the pack's translated texts needs a maintainer.
MAX_LOSS = 0.2


def owners_of(pack_id):
    """The GitHub usernames (lowercase) who can publish updates to this pack, from main's .github/pack-owners.json."""
    try:
        with open(OWNERS, encoding='utf-8') as f:
            names = json.load(f).get('owners', {}).get(pack_id) or []
    except (OSError, ValueError, AttributeError):
        return set()
    return {str(n).strip().lstrip('@').lower() for n in names if str(n).strip()}


def is_owner(pack_id, login):
    return bool(login) and login.strip().lstrip('@').lower() in owners_of(pack_id)


def same_author(a, b):
    if a.get('github') and b.get('github'):
        return str(a['github']).strip().lstrip('@').lower() == str(b['github']).strip().lstrip('@').lower()
    return str(a.get('name', '')).strip().lower() == str(b.get('name', '')).strip().lower()


def translated(pack):
    strings = pack.get('strings')
    if not isinstance(strings, dict):
        return 0
    return sum(1 for v in strings.values() if (v if isinstance(v, str) else isinstance(v, dict) and v.get('other')))


def review_reasons(pack_id, old, new, login, whole_file=True):
    """Why a maintainer has to review this update (empty: the owner can publish it).
    whole_file: the update replaces the pack (a pull request), so texts can disappear; the form merges instead."""
    reasons = []
    if not is_owner(pack_id, login):
        reasons.append('@%s isn\'t one of this pack\'s owners (`.github/pack-owners.json`)' % login)
    if (old.get('code') or '') != (new.get('code') or ''):
        reasons.append('it changes the language code (`%s` → `%s`)' % (old.get('code'), new.get('code')))
    old_authors = [a for a in old.get('authors') or [] if isinstance(a, dict)]
    new_authors = [a for a in new.get('authors') or [] if isinstance(a, dict)]
    gone = [a.get('name') or a.get('github') for a in old_authors if not any(same_author(a, b) for b in new_authors)]
    if gone:
        reasons.append('it removes %s from `authors`' % ', '.join('**%s**' % g for g in gone))
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
    pack_id = os.path.splitext(os.path.basename(argv[0]))[0]
    login = argv[2]
    owner = is_owner(pack_id, login)
    reasons = review_reasons(pack_id, old, new, login) if owner else []
    out = os.environ.get('GITHUB_OUTPUT')
    if out:
        with open(out, 'a', encoding='utf-8') as f:
            f.write('author=%s\nok=%s\n' % (str(owner).lower(), str(owner and not reasons).lower()))
    with open(os.path.join(os.environ.get('RUNNER_TEMP') or tempfile.gettempdir(), 'author-reasons.md'), 'w',
              encoding='utf-8', newline='\n') as f:
        f.write(''.join('- %s\n' % r for r in reasons))
    if not owner:
        print('@%s is not an owner of %s: a maintainer reviews it' % (login, pack_id))
    elif reasons:
        print('@%s owns %s, but a maintainer has to look at it:\n%s' % (login, pack_id, '\n'.join('  ' + r for r in reasons)))
    else:
        print('@%s owns %s and the update keeps its code and people: it can be published' % (login, pack_id))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
