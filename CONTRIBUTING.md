# Translating AntiGem

Thank you for helping. Every text the app shows has a **key** (like `settings.language.appLanguage`) and an
English text. A translation gives its own text for each key; the app shows English for any key it leaves out, so a
half-done translation is already useful.

Before you start, look at the [list of languages](README.md#languages). If your language is there, improving that
pack is usually better than starting a second one. Start a new pack when you want a different style or script
(formal, casual, Latin letters…): give it a **variant**.

---

## Way 1: in the app (no git needed)

1. **Settings → Language → New language.** Fill in the name in English, the name in the language itself, the
   language code (see [Codes and ids](#codes-and-ids)) and whether it's written right to left.
   To improve a downloaded language, tap its **⋮ → Duplicate** instead and work on the copy.
2. Tap the language to show the app in it, then **⋮ → Edit texts**. The **To translate** filter shows what's left.
   Edits save as you type, and the app changes as you go.
3. **⋮ → Name & details:** add yourself under **Made by** (name and GitHub username), plus a short description.
4. **⋮ → Send to the community:** save the file, then open the form on GitHub and attach the file
   (if GitHub refuses a `.json` file, zip it first). A bot replies within a minute with what happens to your file:
   which language it is, every pack of that language that's already here, and whether yours updates one of them
   or becomes a new pack. Then we turn it into a pull request and credit you.

## Way 2: with an AI agent

1. Turn on the **Language** MCP server for your agent (the agent's MCP settings → Language).
2. Settings → Language → **Translate with AI → Copy a prompt**, and paste it into a chat. The agent creates the
   language and translates it batch by batch while you watch the app change.
3. **Read the result.** Machine translations are welcome, but a person who speaks the language must check them.
   Say so in the description, e.g. *"Translated with Claude, reviewed by Sita"*. Then send it as in way 1, step 4.

## Way 3: by hand, with a pull request

1. Copy [`en.json`](en.json) (or, in the app, English **⋮ → Save as a template**) to
   `languages/<code>/<id>.json`, e.g. `languages/ne/ne.json`.
2. Change the header (see [The file](#the-file)), translate the texts and delete the `notes` block.
   You can leave out keys you don't translate.
3. Check it: `python3 tools/build_index.py --check languages/ne/ne.json`
4. Open a pull request. The same check runs on it. A translation pull request may only change files in
   `languages/` (`index.json` and the README table are rebuilt after the merge); ask in an issue for anything else.

---

## The file

```json
{
  "format": "antigem-language",
  "format_version": 2,
  "code": "ne",
  "name": "Nepali",
  "native_name": "नेपाली",
  "variant": "",
  "rtl": false,
  "description": "Everyday Nepali for the whole app.",
  "authors": [
    { "name": "Sita Sharma", "github": "sita-sharma", "role": "Translation" },
    { "name": "Ram Thapa", "github": "ramthapa", "role": "Review" }
  ],
  "based_on": "",
  "app_version": "2.0.5",
  "strings": {
    "common.cancel": "रद्द गर्नुहोस्",
    "files.list.count": { "one": "{count} फाइल", "other": "{count} फाइलहरू" }
  },
  "sources": {
    "common.cancel": "77f8c1d"
  }
}
```

| Field | |
|---|---|
| `format`, `format_version` | Always `"antigem-language"` and `2`. |
| `id` | Optional. If it's there, it must be the file name. |
| `code` | The language code. It must match the folder name, and the app uses it for plural rules. |
| `name` | The language's name in English: `Nepali`. |
| `native_name` | Its name in the language itself: `नेपाली`. |
| `variant` | Optional, short. Tells packs of one language apart: `Formal`, `Latin letters`, `Casual`. |
| `rtl` | `true` for languages written right to left (Arabic, Hebrew, Urdu, Persian…). |
| `description` | One or two lines about the translation (style, how complete, how it was made). |
| `authors` | Everyone who worked on it: `name` (required), `github` (username, optional), `role` (optional: `Translation`, `Review`, `AI`…), `url` (optional, https). |
| `based_on` | Optional: the id of the pack this one started from. The app fills it in when you duplicate a downloaded language. |
| `app_version` | The app version the texts were translated for. The app fills it in. |
| `strings` | `key → text`. Keys you leave out show in English. |
| `sources` | `key → hash` of the English each text was translated from. The app writes it; keep it as it is. It's how outdated texts are found when the English changes. |

Your editor can check the file as you type: [`schema/antigem-language.schema.json`](schema/antigem-language.schema.json).

### Codes and ids

- **Folder = language code** ([BCP 47](https://www.w3.org/International/articles/language-tags/)): `ne`, `hi`,
  `pt-BR`, `zh-Hans`, `sr-Latn`. Use the plain code unless the region or script really matters.
- **File name = pack id**, unique in this repository: lowercase letters, digits and hyphens.
  Use the code for the main pack (`ne.json`, `pt-br.json`) and add the variant for others
  (`hi-latn.json`, `de-formal.json`).
- **One pack per language and variant.** A second pack of a language needs its own `variant` (`Formal`,
  `Latin letters`…). That's also how a translation sent with the form finds its pack: same code and variant means
  an update of that pack, anything else is a new pack.
- **Pull requests change only pack files:** `languages/<code>/<id>.json`. Other files (notes, images, deeper
  folders) are refused.
- **Don't rename a published pack.** The app knows a downloaded language by its id, so a new id is a new language
  for everyone who downloaded the old one.

### Updating your own pack

If you **created** a pack (your GitHub username was in its `authors` when it was first published), you're one of
its **owners**, and your updates to it are published **without waiting for a maintainer**, whichever way you send
them. The owners of each pack are listed in [`.github/pack-owners.json`](.github/pack-owners.json); a maintainer can
add more people there.

- **With the form:** send it with *Share a translation* as usual. It's checked and published within minutes, and
  the issue is closed with a link.
- **With a pull request:** change only your pack's file. When the check passes, it's approved and merged
  automatically.

A maintainer still looks first when the update changes the language code, removes someone from `authors`, or (in a
pull request) drops more than a fifth of the pack's texts. You'll get a comment saying why.

Helping with a pack you didn't create puts you in its `authors`, so the app credits you, but it doesn't make you an
owner: your updates are reviewed by a maintainer.

### Improving an existing pack

Edit its file in a pull request (or duplicate it in the app and send the result: we merge it into the original).
Add yourself to `authors` and keep everyone who's already there.

**Working on an older copy is safe.** The repository keeps every published version of a pack, so we find the one
your copy was made from and take **only the texts you changed** since then:

- texts someone else improved after your copy keep their newer version;
- a text translated from older English never replaces one translated from today's English;
- a text both you and someone else changed since your copy is shown to a maintainer, who picks one.

The bot's reply on a *Share a translation* issue says how many texts are taken and how many stay. A pull request
is merged as it is, so there the bot comments instead when your file would bring back older texts, and lists them for
you to fix. Still, for the
least work, download the language again in an up-to-date app before you start.

---

## Rules for the texts

- **Keep every `{placeholder}` exactly as it is.** The app fills them in: `{name}`, `{count}`, `{path}`. You can move
  them anywhere in the sentence, but don't translate or rename them. A text with a placeholder that isn't in the
  English text is refused.
- **Plurals:** where English has `{ "one": …, "other": … }`, give the forms your language uses
  (`zero`, `one`, `two`, `few`, `many`, `other`; `other` is required). The app picks the form from
  [the language's plural rules](https://www.unicode.org/cldr/charts/latest/supplemental/language_plural_rules.html).
- **Keep buttons, tabs and chips short.** They sit on a phone screen.
- **Don't translate** names of apps, agents, models and services (AntiGem, Claude, Codex, OpenCode, Antigravity,
  Gemini, GitHub, Termux…), commands, file paths and code.
- **Write the way people talk** in your language on their phones. Follow the style your phone's own system uses.
- Line breaks (`\n`) and punctuation like `·` and `…` can stay where they fit your language.
- Some keys have a note in `en.json` (`notes`) that explains where the text appears.

## License

The app is licensed under GPL-3.0-or-later, and so are the translations (see [LICENSE](LICENSE)): they translate
the app's own texts. **By sending a translation (a pull request or the "Share a translation" form), you agree to
share it under GPL-3.0-or-later.** You keep the credit: your name stays in the pack's `authors`.

## For maintainers

- **Owners publish their own updates:** a pull request or a *Share a translation* issue from one of the pack's
  owners in `.github/pack-owners.json` is checked and published without you, unless it changes the code, removes
  authors, or drops many texts (`tools/author_update.py`, `.github/workflows/author-merge.yml`). When a pack is first
  published, its authors at that moment become its owners automatically (so check the `authors` of new packs);
  after that the file only changes by hand: edit it to give someone rights to a pack or take them away.
- **Turning a "Share a translation" issue into a pull request:** add the `create-pr` label to the issue. A workflow
  downloads the attached file and works out from it where it goes (its `code` is the folder; the pack with the
  same code and variant is updated: its texts replace the old ones, every author stays; otherwise it's a new pack),
  checks it, opens a pull request that closes the
  issue and comments the link. If the file has problems, it says what on the issue and removes the label: once the
  issue has a fixed file, add the label again. GitHub holds the check on a pull request opened by the bot: click
  **Approve workflows to run** in its merge box, then review, approve and merge it.
- **`main` is protected:** changes go through a pull request with the `check` passing and an approval. The check
  uses `main`'s own `tools/` and `en.json`, never the pull request's copy. The publish workflow pushes `index.json`
  with a deploy key (the `PUBLISH_DEPLOY_KEY` secret) that the rule lets through.

## What happens after a merge

A workflow checks every pack again, rebuilds `index.json` and the table in the README, and commits them. Within a
few minutes, the app lists the new pack. People who already downloaded it see **Update** the next time they open
the Language page.
