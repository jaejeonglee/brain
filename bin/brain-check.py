#!/usr/bin/python3
"""Brain size, format and index checks, and the end-of-turn commit. Used by the brain plugin's hooks and by hand.

The notebook is brain/ in the folder Claude Code was opened in ($CLAUDE_PROJECT_DIR). It keeps its own git
history (brain/.git), or the history of the folder's repository when brain/ is tracked there.

  --post    PostToolUse hook: check the one edited page, or after Bash every uncommitted page and the log.
            Exit 2 shows the problem to Claude.
  --stop    Stop hook: check everything. Exit 2 keeps Claude working until fixed (once per stop),
            otherwise commit the notebook with the last log line as the message.
  --report  Print every problem and the always-read size; exit 1 if any.
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
    pages = capped_pages()
    problems = []
    for rel in pages:
        problems.extend(check_page(rel))
    problems.extend(check_index(pages))
    problems.extend(log_line_lengths())
    problems.extend(archive_conflicts())
    size = always_read_size()
    if size > ALWAYS_READ_LIMIT:
        problems.append(f"항상 읽는 세 쪽 {size}자 > 상한 {ALWAYS_READ_LIMIT}자 (INDEX, me, now를 줄일 것)")
    return problems, size


def read_hook_input():
    """Hooks pipe JSON in. Run by hand, stdin can stay open and empty, so wait at most a second."""
    try:
        if sys.stdin.isatty() or not select.select([sys.stdin], [], [], 1)[0]:
            return {}
        return json.load(sys.stdin)
    except (ValueError, OSError):
        return {}


def is_checked_page(rel):
    return rel.startswith("brain/") and rel.endswith(".md") and not rel.startswith(("brain/log/", "brain/archive/"))


def git(*args):
    return subprocess.run(["git", "-C", BRAIN, *args], capture_output=True, text=True)


def has_git():
    return git("rev-parse", "--git-dir").returncode == 0


def changed_paths():
    """Uncommitted notebook files, relative to the opened folder."""
    if not has_git():
        return []
    top = git("rev-parse", "--show-toplevel").stdout.strip()
    paths = []
    for line in git("status", "--porcelain", "--untracked-files=all", "--", ".").stdout.splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        paths.append(os.path.relpath(os.path.join(top, path), HOME))
    return paths


def uncommitted_problems():
    """Bash edits skip the Edit|Write hook, so after Bash check every changed page and the log."""
    changed = changed_paths()
    problems = [problem for rel in changed if is_checked_page(rel) for problem in check_page(rel)]
    if any(rel.startswith("brain/log/") for rel in changed):
        problems.extend(log_line_lengths())
    return problems


def git_busy():
    git_dir = git("rev-parse", "--absolute-git-dir").stdout.strip()
    markers = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")
    if git_dir and any(os.path.exists(os.path.join(git_dir, marker)) for marker in markers):
        return True
    return bool(git("diff", "--name-only", "--diff-filter=U").stdout.strip())


def tell_user(message):
    """Stop hook stdout is a debug line unless it is JSON; systemMessage is shown to the user."""
    print(json.dumps({"systemMessage": message}, ensure_ascii=False))


def commit_brain():
    """Commit the notebook only, on main."""
    if not has_git() or git_busy():
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
    if mode == "--post":
        data = read_hook_input()
        if data.get("tool_name") == "Bash":
            problems = uncommitted_problems()
        else:
            path = (data.get("tool_input") or {}).get("file_path") or ""
            rel = os.path.relpath(os.path.realpath(path), HOME) if path else ""
            problems = check_page(rel) if is_checked_page(rel) else []
        if problems:
            print("뇌 규칙 위반, 고칠 것:\n" + "\n".join(problems), file=sys.stderr)
            return 2
        return 0
    if mode == "--stop":
        data = read_hook_input()
        problems, _ = full_check()
        if problems:
            if data.get("stop_hook_active"):
                tell_user("뇌 검사 경고(계속 남아 있어 저장 안 함): " + "; ".join(problems[:3]))
                return 0
            print("턴을 끝내기 전에 뇌 규칙 위반을 고칠 것:\n" + "\n".join(problems[:10])
                  + "\n고친 뒤 확인은 brain-check.py --report로 한다. 저장은 턴이 끝날 때 이 hook이 한다.",
                  file=sys.stderr)
            return 2
        commit_brain()
        return 0
    problems, size = full_check()
    print(f"항상 읽는 세 쪽: {size}자 (목표 {ALWAYS_READ_TARGET}자, 상한 {ALWAYS_READ_LIMIT}자)")
    for problem in problems:
        print(problem)
    print("문제 없음" if not problems else f"문제 {len(problems)}건")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
