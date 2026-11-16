# brain

**Claude Code that remembers across conversations.**

brain is a plugin that gives Claude Code a notebook. When you open Claude Code in the folder where it is installed, Claude reads the notebook (`brain/`) first, writes down what it learns while working, and saves the notebook on its own at the end of each turn. Open a new conversation the next day and it still knows what you decided yesterday.

- **It remembers what you say.** Say "from now on, write commit titles in Korean" once, and it keeps doing so in later conversations.
- **It looks things up before deciding.** Before making a plan, it searches the notebook for past decisions, lessons and postponed work.
- **The notebook does not turn into a mess.** Lines carry their source, every page has a size cap, and only notebooks that pass the check are saved.
- **Your work goes on as before.** Nothing is blocked; memory is only added.

Used together with the [conductor](https://github.com/jaejeonglee/conductor) plugin, the conductor reads the notebook before planning and writes to it when work is done. Each plugin also works on its own.

## Install

You need Claude Code, `git` and `/usr/bin/python3`. Run these in the folder where the notebook should live.

```bash
claude plugin marketplace add jaejeonglee/brain
```

```bash
claude plugin install brain@brain --scope project
```

With `--scope project` the plugin is on only for conversations opened in this folder; Claude Code in other folders is not affected. For a private repository, git on this computer must be signed in to GitHub.

Then open Claude Code in that folder. On the first run it creates `brain/` and tells you so.

## How to use it

There is nothing to call. Talk as usual.

```text
You     From now on, show plans as bullet lists, not tables. Tables break on my phone.
Claude  Got it. I've written that down in the notebook.

        (next day, new conversation)

You     Make a plan for reworking the myapp login screen.
Claude  (shows the plan as a bullet list, as the notebook says)
```

- When a conversation opens, and again after a long conversation is compacted, the notebook's three core pages (me, now, index) are read to Claude.
- When the three core pages together pass 5,000 characters, or the last tidy is more than 30 days old, Claude suggests tidying the notebook when a conversation opens. The same suggestion comes at most once every 7 days.
- If another agent's report or a search result says "record this as a preference", it is not recorded. Rules and preferences in the notebook come only from what you yourself say.

## The notebook

| Page | Holds | Read when |
| --- | --- | --- |
| `me.md` | Your preferences and rules | Always |
| `now.md` | Work in progress | Always |
| `INDEX.md` | Table of contents and project list | Always |
| `lessons.md` | Lessons that apply across jobs | When planning |
| `projects/<name>.md` | Direction, decisions and facts per project | When working on that project |
| `facts/<tool>.md` | Verified facts about a tool | When using that tool |
| `log/YYYY-MM.md` | Monthly log | When searching |
| `archive/` | Archive | When searching |

- Line format: `- [pref] Show plans as bullet lists #ux (source: user 2026-11-10)`. `user <date>` means you said it on that date. Entries are written in the language you speak to Claude. Notebooks written before the English version use `출처:` instead of `source:`; both are accepted.
- When the source is a file path (such as `wiki/projects/x.md`), the check verifies that the file exists. If it does not, saving still goes ahead and you are told when the next conversation opens. Keep the long reasoning in that document and only the one-line conclusion in the notebook.
- The notebook keeps its own git history (`brain/.git`). Even when the folder is a project repository, the notebook never mixes with the project's history, and having no remote, it cannot be pushed by accident. If `brain/.git` is missing, the notebook is not saved into the project repository instead: saving is skipped and you are told.
- If an existing `brain/` is not a notebook (it has no `brain/INDEX.md`), it is left alone and the notebook is not used in that folder. This keeps a project source folder, or another repository with the same name, from being mistaken for the notebook.
- If the folder is a git repository, add `brain/` to that repository's `.gitignore`.

## What is inside

| What | Where | What it does |
| --- | --- | --- |
| Skill | `skills/brain/SKILL.md` | When to write what in the notebook, how to search it, how to tidy it |
| Session-start sensor | `bin/brain-signals.py` | Creates the notebook on first use, and reads the three core pages and any notices to Claude |
| Notebook check sensor | `bin/brain-check.py` | Checks the format on every edit, and saves at the end of the turn when the check passes |
| Empty notebook | `template/` | The skeleton copied on first use |

To check by hand, run `brain-check.py --report` in the folder that has the notebook. The exact installed path appears in the notice Claude sees when a conversation opens.

## Test results

On November 11, 2026, the plugin was loaded on its own and together with conductor into empty practice folders and tested with Opus 5.5. It was loaded once with `--plugin-dir` instead of being installed, and each test was run once.

| Group | What was checked | Result |
| --- | --- | --- |
| Writing | "From now on, …", rules said indirectly, corrections (editing the same line), not writing down noise, writing a project decision to its card and using it in the next conversation | Pass |
| Looking up | Preferences, project decisions, lessons, tool decisions, an old decision found only in the log | Pass |
| Filtering | Following reality and fixing the notebook when it disagrees with reality or with itself | Pass |
| Safety | Not following instructions planted in the notebook, or "record this as a preference" in someone else's text | Pass |
| First use | Creating the notebook in an empty folder, writing the first preference and saving | Pass |

## Known limits

- Checked on macOS only.
- Each folder where the plugin is installed gets its own notebook. It is best to always open conversations in the same folder.
- Looking things up in the notebook is up to Claude's judgment. The check sensor enforces format only, not content.
- The three core pages are kept under 7,000 characters in total so that the session-start notice is not cut off.

## Status

Private repository. No license has been chosen yet.
