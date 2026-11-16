#!/usr/bin/python3
"""Brain size, format and index checks, and the end-of-turn commit. Used by the brain plugin's hooks and by hand.

The notebook is brain/ in the folder Claude Code was opened in ($CLAUDE_PROJECT_DIR). It keeps its own git
history (brain/.git) and is committed only there: a repository that merely encloses brain/ (the project's own)
is never used, so the notebook cannot end up in the project's history.

  --post    PostToolUse hook: check the one edited page, or after Bash every uncommitted page and the log.
            Exit 2 shows the problem to Claude.
  --stop    Stop hook: check everything. Exit 2 keeps Claude working until fixed (once per stop),
            otherwise commit the notebook with the last log line as the message.
  --report  Print every problem and warning and the always-read size; exit 1 if any problem.

A source file that does not exist is a warning, not a problem: it is shown (after an edit of that page, and at
session start) but never blocks the turn or the save.
"""
import json
import os
import re
import select
import subprocess
import sys


def find_home():
    """The folder Claude Code was opened in, or the nearest folder above the shell that has a notebook."""
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        return os.path.realpath(os.environ["CLAUDE_PROJECT_DIR"])
    here = probe = os.path.realpath(os.getcwd())
    while True:
        if os.path.isfile(os.path.join(probe, "brain", "INDEX.md")):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return here
        probe = parent


HOME = find_home()
BRAIN = os.path.join(HOME, "brain")
ALWAYS_READ = ["brain/INDEX.md", "brain/me.md", "brain/now.md"]
ALWAYS_READ_TARGET = 5000  # characters; the session hook prints these pages and hook output is cut near 10,000
ALWAYS_READ_LIMIT = 7000
CAP = re.compile(r"^<!-- cap: (\d+) lines / (\d+) KB -->\s*$")
ENTRY = re.compile(r"^- \[(decision|fact|pref|rule|lesson|open|superseded)\]")
MAX_LINE = 200
CONFLICT = re.compile(r"^(<{7}|={7}|>{7})( |$)")
# The source field is "(source: ..." in English notebooks and "(출처: ..." in notebooks written before the plugin
# was translated. Both spellings are accepted so that existing notebooks keep passing.
SOURCE = re.compile(r"\((?:[Ss]ource|출처):\s*([^;)]*)")
HAS_SOURCE = re.compile(r"(?:[Ss]ource|출처):")
# Only path-like pieces of the source field are checked: a slash, and a common document or code extension at the
# end (an #anchor or :line is allowed). Commit hashes, session:id and "user 2026-11-12" are left alone.
PATHLIKE = re.compile(r"^(~?/?[\w.@+-]+(?:/[\w.@+-]+)+\.(?:md|txt|py|js|ts|tsx|json|ya?ml|sh|toml))(?:#\S*|:\d+)?$")


def source_paths(line):
    match = SOURCE.search(line)
    if not match:
        return []
    found = []
    for token in re.split(r"[,\s]+", match.group(1).strip()):
        hit = PATHLIKE.match(token.strip("`'\""))
        if hit:
            found.append(hit.group(1))
    return found


def source_exists(path):
    """~/ and absolute paths as written; anything else from the opened folder (wiki/projects/x.md → HOME/wiki/...)."""
    if path.startswith("~"):
        return os.path.exists(os.path.expanduser(path))
    if path.startswith("/"):
        return os.path.exists(path)
    return os.path.exists(os.path.join(HOME, path))


def capped_pages():
    pages = []
    for root, dirs, files in os.walk(BRAIN):
        dirs[:] = [name for name in dirs if name != ".git"]
        rel_root = os.path.relpath(root, HOME)
        if rel_root.startswith(("brain/log", "brain/archive")):
            continue
        for name in sorted(files):
            if name.endswith(".md"):
                pages.append(os.path.join(rel_root, name))
    return sorted(pages)


def check_page(rel):
    problems = []
    try:
        with open(os.path.join(HOME, rel), encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as error:
        return [f"{rel}: cannot read ({error.__class__.__name__})"]
    lines = text.splitlines()
    conflict = [number for number, line in enumerate(lines, 1) if CONFLICT.match(line)]
    if conflict:
        return [f"{rel}:{conflict[0]}: merge conflict markers left"]
    match = CAP.match(lines[0]) if lines else None
    if not match:
        return [f"{rel}: first line is not '<!-- cap: N lines / M KB -->'"]
    max_lines, max_kb = int(match.group(1)), int(match.group(2))
    if len(lines) > max_lines:
        problems.append(f"{rel}: {len(lines)} lines > cap {max_lines} lines (split by topic or shorten)")
    if len(text.encode("utf-8")) > max_kb * 1024:
        problems.append(f"{rel}: {len(text.encode('utf-8'))} B > cap {max_kb} KB (split by topic or shorten)")
    for number, line in enumerate(lines, 1):
        if not ENTRY.match(line):
            continue
        if len(line) > MAX_LINE:
            problems.append(f"{rel}:{number}: line of {len(line)} characters > {MAX_LINE}")
        if line.startswith(("- [fact]", "- [decision]")) and not HAS_SOURCE.search(line):
            problems.append(f"{rel}:{number}: [fact]/[decision] line has no source")
    return problems


def missing_sources(rel):
    """Entry lines whose source file is not there. A warning, never a problem: the file may have been moved by
    someone else and only the user knows where it went, so it must not hold up the turn or the save."""
    try:
        with open(os.path.join(HOME, rel), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return []  # check_page() already reports a page it cannot read
    return [f"{rel}:{number}: source file not found '{path}' (if it moved, fix the source)"
            for number, line in enumerate(lines, 1) if ENTRY.match(line)
            for path in source_paths(line) if not source_exists(path)]


def check_index(pages):
    try:
        with open(os.path.join(BRAIN, "INDEX.md"), encoding="utf-8", errors="replace") as handle:
            index = handle.read()
    except OSError:
        return ["brain/INDEX.md: missing"]
    return [f"brain/INDEX.md: no entry for '{rel}'" for rel in pages if rel != "brain/INDEX.md" and rel not in index]


def log_line_lengths():
    problems = []
    folder = os.path.join(BRAIN, "log")
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not name.endswith(".md"):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, "rb") as handle:
                handle.read().decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"brain/log/{name}: contains bytes that are not UTF-8")
        with open(path, encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, 1):
                if CONFLICT.match(line):
                    problems.append(f"brain/log/{name}:{number}: merge conflict markers left")
                length = len(line.rstrip("\n"))  # the same length in the test and in the message
                if length > MAX_LINE:
                    problems.append(f"brain/log/{name}:{number}: line of {length} characters > {MAX_LINE}")
    return problems


def archive_conflicts():
    problems = []
    for root, dirs, files in os.walk(os.path.join(BRAIN, "archive")):
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, HOME)
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for number, line in enumerate(handle, 1):
                        if CONFLICT.match(line):
                            problems.append(f"{rel}:{number}: merge conflict markers left")
                            break
            except OSError as error:
                problems.append(f"{rel}: cannot read ({error.__class__.__name__})")
    return problems


def always_read_size():
    total = 0
    for rel in ALWAYS_READ:
        try:
            with open(os.path.join(HOME, rel), encoding="utf-8", errors="replace") as handle:
                total += len(handle.read())
        except OSError:
            pass
    return total


def full_check():
    """Problems (they block the save), warnings (reported, never blocking) and the always-read size."""
    pages = capped_pages()
    problems, warnings = [], []
    for rel in pages:
        problems.extend(check_page(rel))
        warnings.extend(missing_sources(rel))
    problems.extend(check_index(pages))
    problems.extend(log_line_lengths())
    problems.extend(archive_conflicts())
    size = always_read_size()
    if size > ALWAYS_READ_LIMIT:
        problems.append(f"always-read pages {size} characters > limit {ALWAYS_READ_LIMIT} (shorten INDEX, me, now)")
    return problems, warnings, size


def read_hook_input():
    """Hooks pipe JSON in. Run by hand, stdin can stay open and empty, so wait at most a second."""
    try:
        if sys.stdin.isatty() or not select.select([sys.stdin], [], [], 1)[0]:
            return {}
        return json.load(sys.stdin)
    except (ValueError, OSError):
        return {}


def notebook_rel(path):
    """An edited file as "brain/..." when it is in the notebook, also when brain/ is a link to a folder elsewhere;
    otherwise relative to the opened folder."""
    real = os.path.realpath(path)
    inside = os.path.relpath(real, os.path.realpath(BRAIN))
    if inside != os.pardir and not inside.startswith(os.pardir + os.sep):
        return os.path.join("brain", inside)
    return os.path.relpath(real, HOME)


def is_checked_page(rel):
    return rel.startswith("brain/") and rel.endswith(".md") and not rel.startswith(("brain/log/", "brain/archive/"))


# git takes these from the environment before anything else. Set by whoever started Claude Code (a git hook,
# a dotfiles shell), they would point every command below at some other repository.
GIT_ENV = {key: value for key, value in os.environ.items() if key not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}


def git(*args):
    # utf-8 whatever the shell's locale; surrogateescape keeps any odd byte in a file name as it is.
    return subprocess.run(["git", "-C", BRAIN, *args], capture_output=True, encoding="utf-8", errors="surrogateescape",
                          env=GIT_ENV)


def repo_top():
    """Top folder of the repository git finds from brain/: its own, or a project's around it. "" if none."""
    top = git("rev-parse", "--show-toplevel")
    return top.stdout.rstrip("\n") if top.returncode == 0 else ""


def own_git(top=None):
    """True only when brain/ is the top of its own repository. Without brain/.git, git answers for the project
    repository that encloses brain/, and committing there would put the notebook into the project's history."""
    top = repo_top() if top is None else top
    try:
        # samefile, not a string compare: brain/ may be a link, and macOS may spell the path in another case.
        return bool(top) and os.path.samefile(top, BRAIN)
    except OSError:
        return False


def no_own_git_reason():
    """Why the notebook has no usable history of its own, in words for the user."""
    if not os.path.lexists(os.path.join(BRAIN, ".git")):
        return "brain/ has no git history of its own (brain/.git). Run git init -b main in brain/ to resume saving"
    complaint = git("rev-parse", "--show-toplevel").stderr.strip().splitlines()
    return f"git cannot use brain/.git ({complaint[0] if complaint else 'it looks like part of another repository'})"


def is_notebook():
    """brain/ is the notebook only when it has the notebook's index. A project folder, a library clone or a
    submodule that merely happens to be called brain/ is left alone, even with a git history of its own."""
    return os.path.isfile(os.path.join(BRAIN, "INDEX.md"))


def changed_paths():
    """Uncommitted notebook files, deleted ones included, relative to the opened folder ("brain/..."). Read from
    brain/'s own repository or, for checking only (never for committing), from a project repository around it."""
    top = repo_top()
    if not top:
        return []
    own = own_git(top)
    # -z: every name comes exactly as stored, NUL-separated. Without it git wraps non-ASCII names (Korean page
    # names) in quotes with octal escapes. Names are relative to the repository top.
    fields = git("status", "--porcelain", "-z", "--untracked-files=all", "--", ".").stdout.split("\0")
    paths, at = [], 0
    while at < len(fields):
        entry, at = fields[at], at + 1
        if len(entry) < 4:  # the empty piece after the last NUL
            continue
        if "R" in entry[:2] or "C" in entry[:2]:
            at += 1  # a rename or copy is followed by its old name, which is no longer on disk
        name = entry[3:]
        # In its own repository every name is inside brain/ already, also when brain/ is a link to elsewhere.
        paths.append(os.path.join("brain", name) if own
                     else os.path.relpath(os.path.realpath(os.path.join(top, name)), HOME))
    return paths


def uncommitted_problems():
    """Bash edits skip the Edit|Write hook, so after Bash check every changed page and the log."""
    changed = changed_paths()
    # A page that was moved or deleted is gone from disk: nothing to check, and it is not a problem.
    problems = [problem for rel in changed if is_checked_page(rel) and os.path.isfile(os.path.join(HOME, rel))
                for problem in check_page(rel)]
    if any(rel.startswith("brain/log/") for rel in changed):
        problems.extend(log_line_lengths())
    return problems


def git_busy():
    git_dir = git("rev-parse", "--absolute-git-dir").stdout.strip()
    # sequencer: a multi-commit cherry-pick or revert still has commits to go after one conflict is committed.
    markers = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer")
    if git_dir and any(os.path.exists(os.path.join(git_dir, marker)) for marker in markers):
        return True
    return bool(git("diff", "--name-only", "--diff-filter=U").stdout.strip())


def tell_user(message):
    """Stop hook stdout is a debug line unless it is JSON; systemMessage is shown to the user."""
    print(json.dumps({"systemMessage": message}, ensure_ascii=False))


def commit_brain():
    """Commit the notebook only, in its own repository, on main."""
    if not own_git():
        # Never commit into a repository that merely encloses brain/. Tell the user only when something is
        # left unsaved, not at the end of every turn; the session-start signal covers the rest.
        if changed_paths():
            tell_user("Notebook auto-save skipped: " + no_own_git_reason())
        return
    if git_busy():
        return
    branch = git("symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
    if branch != "main":
        tell_user(f"Notebook auto-save skipped: the notebook's git is not on main ({branch or 'detached HEAD'})")
        return
    changed = changed_paths()
    if not changed:
        return
    pages = sorted({os.path.basename(path.rstrip("/")) for path in changed})
    message = "brain: update " + ", ".join(pages)[:100]
    log_dir = os.path.join(BRAIN, "log")
    logs = sorted(name for name in os.listdir(log_dir) if name.endswith(".md")) if os.path.isdir(log_dir) else []
    if logs and any(path.startswith("brain/log") for path in changed):
        with open(os.path.join(log_dir, logs[-1]), encoding="utf-8", errors="replace") as handle:
            entries = [line.strip() for line in handle if line.startswith("- ")]
        if entries:
            message = entries[-1][2:][:120]
    git("add", "-A", "--", ".")
    result = git("commit", "-q", "-m", message, "--", ".")
    if result.returncode != 0:
        tell_user("Notebook auto-save failed: " + (result.stderr.strip().splitlines() or ["?"])[0])


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--report"
    if not os.path.isdir(BRAIN):
        if mode == "--report":
            print(f"No notebook: {BRAIN}")
            return 1
        return 0
    if not is_notebook():
        if mode == "--report":
            print(f"Not a notebook: {BRAIN} (no brain/INDEX.md)")
            return 1
        return 0
    if mode == "--post":
        data = read_hook_input()
        if data.get("tool_name") == "Bash":
            # Missing source files are left to the session-start signal here: after Bash every uncommitted page
            # is looked at, so one moved file would be repeated after every command.
            problems, warnings = uncommitted_problems(), []
        else:
            path = (data.get("tool_input") or {}).get("file_path") or ""
            rel = notebook_rel(path) if path else ""
            checked = is_checked_page(rel)
            problems = check_page(rel) if checked else []
            warnings = missing_sources(rel) if checked else []  # most likely a path Claude has just written
        message = []
        if problems:
            message.append("Brain rule violations, fix them:\n" + "\n".join(problems))
        if warnings:
            message.append("Brain warnings (saving is not blocked). If you wrote a wrong path, fix it; if the file was"
                           " moved and you do not know where, tell the user:\n" + "\n".join(warnings))
        if message:
            print("\n".join(message), file=sys.stderr)  # exit 2 after a tool only shows this to Claude
            return 2
        return 0
    if mode == "--stop":
        data = read_hook_input()
        problems, _, _ = full_check()  # warnings never hold up the turn or the save
        # The hook cannot commit in the middle of a merge, and nothing else would say so: the notebook would
        # silently stop being saved. Claude hears it with the first block of the turn (usually the conflict
        # markers themselves), the user if it is still open on the retry.
        busy = own_git() and git_busy()
        for_claude = ("A merge, cherry-pick or rebase is in progress in the notebook's git, so the hook cannot save:"
                      " if it is the weekly tidy merge, fix the conflicts and finish it yourself with"
                      " git -C brain commit; otherwise tell the user.")
        for_user = ("Notebook auto-save skipped: a merge, cherry-pick or rebase is in progress in the notebook's git"
                    " (see git status in brain/)")
        if problems:
            if data.get("stop_hook_active"):
                tell_user("Notebook auto-save skipped: brain check problems remain: " + "; ".join(problems[:3]))
                return 0
            print("Fix these brain rule violations before ending the turn:\n" + "\n".join(problems[:10]) + "\n"
                  + (for_claude if busy else "Check with brain-check.py --report after fixing. Saving is done by"
                     " this hook at the end of the turn."),
                  file=sys.stderr)
            return 2
        if busy:
            if data.get("stop_hook_active"):
                tell_user(for_user)
                return 0
            print(for_claude, file=sys.stderr)
            return 2
        commit_brain()
        return 0
    problems, warnings, size = full_check()
    # brain-signals.py reads this output: the first line by its exact shape, warnings by the "warning: " prefix,
    # and the last line by the "Result:" prefix. Change them together.
    print(f"Always-read pages: {size} characters (target {ALWAYS_READ_TARGET}, limit {ALWAYS_READ_LIMIT})")
    for problem in problems:
        print(problem)
    for warning in warnings:
        print("warning: " + warning)
    summary = "Result: no problems" if not problems else f"Result: {len(problems)} problem(s)"
    print(summary + (f" ({len(warnings)} warning(s), saving is not blocked)" if warnings else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
