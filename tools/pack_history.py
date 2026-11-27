#!/usr/bin/env python3
"""
Compares an update of a pack with the pack's published history, so an update made on an old copy can't undo newer
texts.

The repository has every published version of a pack. The version the update matches best is the one its sender
started from (their "base"). Then, text by text:

    changed        the sender changed it (it differs from their base)            → taken
    kept_newer     unchanged since their base, but the pack changed it later    → the pack's newer text stays
    older_english  translated from older English than the pack's text           → the pack's text stays
    conflicts      changed by the sender AND in the pack since their base        → a maintainer decides

    python3 tools/pack_history.py <pack path, e.g. languages/ru/ru.json> <updated file> [--ref main] [--note FILE]

prints that summary (as GitHub warnings in Actions when texts would be lost); --note adds, when texts would be lost,
a Markdown section for a comment on the pull request to FILE. Needs Python 3.8+ and git.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_index import ROOT, load_english, text_hash  # noqa: E402

MAX_VERSIONS = 60


def _git(*args):
    try:
        return subprocess.run(['git', '-C', ROOT] + list(args), capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def history(rel, ref='HEAD'):
    """[(commit, date, pack)] for the versions of this pack in the history of ref, newest first."""
    out = _git('log', '--format=%H %cs', '-n', str(MAX_VERSIONS), ref, '--', rel)
    versions = []
    for line in (out or b'').decode().splitlines():
        sha, _, date = line.partition(' ')
        raw = _git('show', '%s:%s' % (sha, rel))
        try:
            o = json.loads(raw.decode('utf-8-sig')) if raw else None
        except ValueError:
            continue
        if isinstance(o, dict) and isinstance(o.get('strings'), dict):
            versions.append((sha, date, o))
    return versions


def find_base(strings, versions):
    """The version the update was made from: the one with the most texts equal to the update's (newest on a tie)."""
    best, best_score = None, -1
    for v in versions:
        s = v[2].get('strings') or {}
        score = sum(1 for k, t in strings.items() if s.get(k) == t)
        if score > best_score:
            best, best_score = v, score
    return best


def compare(current, incoming, versions, english=None):
    """Sorts the update's texts (see the top of this file). current: the pack now; incoming: the update."""
    english = english if english is not None else load_english()
    base = find_base(incoming.get('strings') or {}, versions)
    bs = (base[2].get('strings') or {}) if base else (current.get('strings') or {})
    cs = current.get('strings') or {}
    csrc, isrc = current.get('sources') or {}, incoming.get('sources') or {}
    res = {'base': base[0] if base else None, 'base_date': base[1] if base else None,
           'changed': [], 'kept_newer': [], 'older_english': [], 'conflicts': []}
    for k, t in (incoming.get('strings') or {}).items():
        cur = cs.get(k)
        if t == cur:
            continue
        if k in bs and t == bs[k]:
            res['kept_newer'].append(k)        # the sender didn't touch it; the pack has a newer text
            continue
        en = english.get(k)
        if en is not None and isrc.get(k) and csrc.get(k) == text_hash(en) and isrc[k] != text_hash(en):
            res['older_english'].append(k)     # the pack's text follows today's English, the sender's doesn't
            continue
        if cur is not None and bs.get(k) != cur:
            res['conflicts'].append(k)         # both changed it since the sender's copy
            continue
        res['changed'].append(k)
    return res


def version_key(v):
    """Orders app versions: 2.0.5-beta.7 < 2.0.5-beta.8 < 2.0.5 < 2.0.6."""
    v = str(v or '').strip().lstrip('vV')
    main, _, pre = v.partition('-')
    nums = tuple(int(x) if x.isdigit() else 0 for x in main.split('.') if x)
    if not pre:
        return nums, (1,)
    return nums, (0,) + tuple((0, int(x), '') if x.isdigit() else (1, 0, x) for x in re.split(r'[.\-]', pre))


def older_app(incoming, current):
    """True when the update comes from an older app version than the pack's."""
    a, b = incoming.get('app_version'), current.get('app_version')
    return bool(a and b) and version_key(a) < version_key(b)


def summary(res, incoming, current, keys_shown=8):
    """Markdown lines about what's taken and what stays."""
    def keys(ks):
        shown = ', '.join('`%s`' % k for k in ks[:keys_shown])
        return shown + (' and %d more' % (len(ks) - keys_shown) if len(ks) > keys_shown else '')
    lines = []
    if res['base']:
        lines.append('Your file matches the version of this pack from %s (`%s`), so that\'s compared with what\'s '
                     'published now:' % (res['base_date'], res['base'][:7]))
    lines.append('- ✏️ **%d text%s you changed** %s taken.' % (
        len(res['changed']), '' if len(res['changed']) == 1 else 's', 'is' if len(res['changed']) == 1 else 'are'))
    if res['kept_newer']:
        lines.append('- 🕰️ **%d text%s in your file %s older** than the published ones (changed here after your copy): '
                     'the published ones stay.' % (len(res['kept_newer']), '' if len(res['kept_newer']) == 1 else 's',
                                                  'is' if len(res['kept_newer']) == 1 else 'are'))
    if res['older_english']:
        n = len(res['older_english'])
        lines.append('- 🇬🇧 **%d text%s translated from older English** than the published ones: the published ones '
                     'stay.' % (n, ' was' if n == 1 else 's were'))
    if res['conflicts']:
        lines.append('- ⚠️ **%d text%s changed both by you and here since your copy:** %s. A maintainer decides which '
                     'to keep.' % (len(res['conflicts']), '' if len(res['conflicts']) == 1 else 's',
                                   keys(res['conflicts'])))
    if older_app(incoming, current):
        lines.append('- Your file comes from AntiGem %s, and this pack is already at %s. Updating the app and '
                     'downloading the language again gives you the newest texts to start from.'
                     % (incoming.get('app_version'), current.get('app_version')))
    return lines


def pr_note(rel, res, keys_shown=10):
    """Markdown for a pull request that changes this pack, or [] when nothing would be lost. Unlike the form, a pull
    request is merged as it is, so this asks its author to fix it."""
    if not (res['kept_newer'] or res['older_english'] or res['conflicts']):
        return []
    def keys(ks):
        shown = ', '.join('`%s`' % k for k in ks[:keys_shown])
        return shown + (' and %d more' % (len(ks) - keys_shown) if len(ks) > keys_shown else '')
    def n(ks, one, many):
        return '%d %s' % (len(ks), one if len(ks) == 1 else many)
    lines = ['**`%s`**: your copy matches the version from %s (`%s`).' % (
        rel, res['base_date'], (res['base'] or '')[:7]) if res['base'] else '**`%s`**:' % rel, '']
    if res['changed']:
        lines.append('- ✏️ %s you changed: fine.' % n(res['changed'], 'text', 'texts'))
    if res['kept_newer']:
        lines.append('- 🕰️ **%s back to an older version** (changed in the pack after your copy): %s'
                     % (n(res['kept_newer'], 'text would go', 'texts would go'), keys(res['kept_newer'])))
    if res['older_english']:
        lines.append('- 🇬🇧 **%s from older English** than the published one: %s'
                     % (n(res['older_english'], 'text is translated', 'texts are translated'),
                        keys(res['older_english'])))
    if res['conflicts']:
        lines.append('- ⚠️ **%s both by you and in the pack since your copy:** %s'
                     % (n(res['conflicts'], 'text changed', 'texts changed'), keys(res['conflicts'])))
    return lines + ['']


def main(argv):
    ref, note = 'HEAD', None
    for flag in ('--ref', '--note'):
        if flag in argv:
            i = argv.index(flag)
            if flag == '--ref':
                ref = argv[i + 1]
            else:
                note = argv[i + 1]
            argv = argv[:i] + argv[i + 2:]
    if len(argv) != 2:
        sys.exit(__doc__)
    rel, path = argv
    with open(path, encoding='utf-8-sig') as f:
        incoming = json.load(f)
    versions = history(rel, ref)
    if not versions:
        print('%s: a new pack, nothing to compare' % rel)
        return 0
    current = versions[0][2]
    res = compare(current, incoming, versions)
    if note:
        lines = pr_note(rel, res)
        if lines:
            with open(note, 'a', encoding='utf-8', newline='\n') as f:
                f.write('\n'.join(lines) + '\n')
    print('%s: compared with the version from %s (%s): %d changed, %d older than published, %d from older English, '
          '%d conflicts' % (rel, res['base_date'], (res['base'] or '')[:7], len(res['changed']),
                            len(res['kept_newer']), len(res['older_english']), len(res['conflicts'])))
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        if res['kept_newer']:
            print('::warning file=%s::This update brings back %d older texts (changed in the pack after the copy it was '
                  'made from), e.g. %s' % (rel, len(res['kept_newer']), ', '.join(res['kept_newer'][:5])))
        if res['older_english']:
            print('::warning file=%s::%d texts were translated from older English than the published ones, e.g. %s'
                  % (rel, len(res['older_english']), ', '.join(res['older_english'][:5])))
        if res['conflicts']:
            print('::warning file=%s::%d texts were changed both here and in the pack since the copy it was made from, '
                  'e.g. %s' % (rel, len(res['conflicts']), ', '.join(res['conflicts'][:5])))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
