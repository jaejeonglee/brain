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
SOURCE = re.compile(r"\(출처:\s*([^;)]*)")
# 출처 칸에서 «파일 경로처럼 생긴 것»만 검사한다: 슬래시가 있고 흔한 문서·코드 확장자로 끝나는 것(#앵커·:줄번호 허용).
# 커밋 해시, session:id, «사용자 2026-11-12» 같은 출처는 건드리지 않는다.
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
    """경로는 ~/·절대경로 그대로, 그 밖에는 수첩을 연 폴더 기준으로 찾는다(예: wiki/projects/x.md → HOME/wiki/...)."""
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
        return [f"{rel}: 읽을 수 없음 ({error.__class__.__name__})"]
    lines = text.splitlines()
    conflict = [number for number, line in enumerate(lines, 1) if CONFLICT.match(line)]
    if conflict:
        return [f"{rel}:{conflict[0]}: 병합 충돌 표시가 남아 있음"]
    match = CAP.match(lines[0]) if lines else None
    if not match:
        return [f"{rel}: 첫 줄에 '<!-- cap: N lines / M KB -->'가 없음"]
    max_lines, max_kb = int(match.group(1)), int(match.group(2))
    if len(lines) > max_lines:
        problems.append(f"{rel}: {len(lines)}줄 > 상한 {max_lines}줄 (주제별로 쪼개거나 줄일 것)")
    if len(text.encode("utf-8")) > max_kb * 1024:
        problems.append(f"{rel}: {len(text.encode('utf-8'))}B > 상한 {max_kb}KB (주제별로 쪼개거나 줄일 것)")
    for number, line in enumerate(lines, 1):
        if not ENTRY.match(line):
            continue
        if len(line) > MAX_LINE:
            problems.append(f"{rel}:{number}: 한 줄 {len(line)}자 > {MAX_LINE}자")
        if line.startswith(("- [fact]", "- [decision]")) and "출처:" not in line:
            problems.append(f"{rel}:{number}: [fact]/[decision] 줄에 출처가 없음")
    return problems


def missing_sources(rel):
    """Entry lines whose source file is not there. A warning, never a problem: the file may have been moved by
    someone else and only the user knows where it went, so it must not hold up the turn or the save."""
    try:
        with open(os.path.join(HOME, rel), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return []  # check_page() already reports a page it cannot read
    return [f"{rel}:{number}: 출처 파일이 없음 '{path}' (옮겼으면 출처를 고칠 것)"
            for number, line in enumerate(lines, 1) if ENTRY.match(line)
            for path in source_paths(line) if not source_exists(path)]


def check_index(pages):
    try:
        with open(os.path.join(BRAIN, "INDEX.md"), encoding="utf-8", errors="replace") as handle:
            index = handle.read()
    except OSError:
        return ["brain/INDEX.md: 없음"]
    return [f"brain/INDEX.md: '{rel}' 줄이 없음" for rel in pages if rel != "brain/INDEX.md" and rel not in index]


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
            problems.append(f"brain/log/{name}: UTF-8이 아닌 바이트가 있음")
        with open(path, encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, 1):
                if CONFLICT.match(line):
                    problems.append(f"brain/log/{name}:{number}: 병합 충돌 표시가 남아 있음")
                if len(line.rstrip("\n")) > MAX_LINE:
                    problems.append(f"brain/log/{name}:{number}: 한 줄 {len(line.rstrip())}자 > {MAX_LINE}자")
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
                            problems.append(f"{rel}:{number}: 병합 충돌 표시가 남아 있음")
                            break
            except OSError as error:
                problems.append(f"{rel}: 읽을 수 없음 ({error.__class__.__name__})")
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
        problems.append(f"항상 읽는 세 쪽 {size}자 > 상한 {ALWAYS_READ_LIMIT}자 (INDEX, me, now를 줄일 것)")
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
        return "brain/에 자체 git 기록(brain/.git)이 없음. brain/에서 git init -b main 하면 다시 저장한다"
    complaint = git("rev-parse", "--show-toplevel").stderr.strip().splitlines()
    return f"brain/.git을 git이 쓰지 못함({complaint[0] if complaint else '다른 저장소의 일부로 보임'})"


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
            tell_user("수첩 자동 저장 건너뜀: " + no_own_git_reason())
        return
    if git_busy():
        return
    branch = git("symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
    if branch != "main":
        tell_user(f"수첩 자동 저장 건너뜀: 수첩 git이 main이 아님 ({branch or 'detached HEAD'})")
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
        tell_user("수첩 자동 저장 실패: " + (result.stderr.strip().splitlines() or ["?"])[0])


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--report"
    if not os.path.isdir(BRAIN):
        if mode == "--report":
            print(f"수첩 없음: {BRAIN}")
            return 1
        return 0
    if not is_notebook():
        if mode == "--report":
            print(f"수첩 아님: {BRAIN} (brain/INDEX.md 없음)")
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
            message.append("뇌 규칙 위반, 고칠 것:\n" + "\n".join(problems))
        if warnings:
            message.append("뇌 경고(저장은 막지 않음). 경로를 잘못 적었으면 고치고, 파일이 옮겨졌는데 어디인지 모르면"
                           " 사용자에게 알린다:\n" + "\n".join(warnings))
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
        for_claude = ("수첩 git에서 병합·cherry-pick·rebase가 진행 중이라 hook이 저장하지 못한다: 주간 정리 합치기라면"
                      " 충돌을 고친 뒤 git -C brain commit 으로 직접 마치고, 아니면 사용자에게 알린다.")
        for_user = "수첩 자동 저장 건너뜀: 수첩 git에서 병합·cherry-pick·rebase가 진행 중 (brain/에서 git status로 확인)"
        if problems:
            if data.get("stop_hook_active"):
                tell_user("수첩 자동 저장 건너뜀: 뇌 검사 문제가 남아 있음: " + "; ".join(problems[:3]))
                return 0
            print("턴을 끝내기 전에 뇌 규칙 위반을 고칠 것:\n" + "\n".join(problems[:10]) + "\n"
                  + (for_claude if busy else "고친 뒤 확인은 brain-check.py --report로 한다. 저장은 턴이 끝날 때 이 hook이 한다."),
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
    print(f"항상 읽는 세 쪽: {size}자 (목표 {ALWAYS_READ_TARGET}자, 상한 {ALWAYS_READ_LIMIT}자)")
    for problem in problems:
        print(problem)
    for warning in warnings:
        print("경고: " + warning)  # brain-signals.py picks these lines out by this prefix
    summary = "문제 없음" if not problems else f"문제 {len(problems)}건"
    print(summary + (f" (경고 {len(warnings)}건, 저장은 막지 않음)" if warnings else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
