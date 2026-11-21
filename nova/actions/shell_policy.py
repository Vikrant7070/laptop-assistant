"""Is this shell command read-only, or could it change something?

    classify("Get-Process | Sort-Object CPU -Descending")   -> "read"
    classify("Get-Item a; Remove-Item b")                   -> "write"

Decision (2026-11-21, docs/improvement-roadmap.md R2): a `run_powershell` / `run_in_terminal` command that is not
provably read-only **always asks**, at every autonomy level including "full", with no setting to turn that off.
`session.is_risky` applies it.

The rule is deny-by-default: a command is "read" only if every command in it is on the read-only list and none of the
tricks below appears. Getting a harmless command wrong costs one extra question; getting a changing one wrong could
cost a file. So when in doubt, "write".

How it reads a command:
1. Anything that can run hidden code or write is "write" wherever it appears: a `>`/`>>` redirection (except to
   `$null`, and stream merges like `2>&1`), `-EncodedCommand`, `iex`/`Invoke-*`, `Start-Process`, the `&` call
   operator, a backtick (line continuation, or escaping a name: Re`move-Item), a `$(` subexpression, `${...}`
   provider variables, `::` static calls, `.Method(` calls, and `-Force`.
2. The text is cut into pieces at `|`, `;`, `&&`, `||`, newlines, `{`, `}`, `(` and `)` (so a script block or a
   `( ... )` group is checked as well), and at an `=` (what follows it is what runs).
3. A piece that starts like an expression (`$x`, `'text'`, `5`, `-gt`, `[int]`, `@`) runs nothing by itself. A piece
   that starts with a bare word runs that command, which must be read-only.
"""
import re

SHELL_TOOLS = ("run_powershell", "run_in_terminal")

# PowerShell cmdlets and Windows programs that only look. Get-* and Format-* are matched by pattern below.
READ_ONLY = {
    "test-path", "test-connection", "resolve-path", "select-object", "where-object", "sort-object", "group-object",
    "measure-object", "out-string", "out-null", "convertto-json", "convertto-csv", "select-string",
    "whoami", "hostname", "systeminfo", "ver", "where.exe", "write-output", "write-host",
    # common aliases of the cmdlets above (PowerShell resolves them to the same thing)
    "gci", "ls", "dir", "gc", "cat", "type", "gi", "gp", "gps", "ps", "gsv", "gcm", "gal", "gm", "pwd",
    "select", "where", "?", "sort", "group", "measure", "ft", "fl", "fw", "sls", "echo",
}
NOT_READ = {"get-credential"}           # a Get-* that prompts for a password

# Programs with arguments: read-only only with these arguments.
IPCONFIG_OK = {"/all", "/displaydns", "/allcompartments", "/?"}        # /release, /renew, /flushdns change things
VERSION_PROGRAMS = {"python", "py", "python3", "node", "npm", "npx", "pip", "pip3", "git", "java", "dotnet", "code",
                    "winget", "ollama", "gh", "docker", "nvidia-smi", "pwsh", "powershell", "cmd"}
VERSION_FLAGS = {"--version", "-v", "-V", "/?", "--help", "-h"}
GIT_READ = {"status", "log", "diff", "show", "blame", "shortlog", "ls-files", "rev-parse", "describe"}
GIT_BRANCH_FLAGS = {"-a", "--all", "-v", "-vv", "-r", "--remotes", "--list", "--show-current"}
PROGRAM_READ = {                         # program -> first arguments that only look
    "pip": {"list", "show", "freeze"}, "pip3": {"list", "show", "freeze"},
    "npm": {"ls", "list", "view", "outdated"}, "winget": {"list", "search", "show"},
    "ollama": {"list", "ps", "show"}, "gh": {"status"},
}

# Step 1: constructs that make the whole command "write", wherever they are.
_HARMLESS_REDIRECT = re.compile(r"[\d*]?>\s*\$null\b|[\d*]>&[12]", re.I)
ALWAYS_WRITE = [
    (re.compile(r">"), "redirects output to a file"),
    (re.compile(r"(?:^|\s)-(?:e|ec|en|enc|enco\w*)(?:\s|$)|-encodedcommand", re.I), "runs encoded hidden code"),
    (re.compile(r"\biex\b|\binvoke-\w+", re.I), "runs other code (Invoke-*)"),
    (re.compile(r"\bstart-process\b|\bsaps\b|(?:^|[\s;|({])start(?:\s|$)", re.I), "starts another program"),
    (re.compile(r"(?<![&>])&(?!&)"), "uses the & call operator"),
    (re.compile(r"`"), "uses a backtick (continuation or escaping)"),
    (re.compile(r"\$\("), "runs a $( ) subexpression"),
    (re.compile(r"\$\{"), "uses a ${ } provider variable"),
    (re.compile(r"::"), "calls a .NET static method"),
    (re.compile(r"\.\s*[A-Za-z_]\w*\s*\("), "calls a .NET method"),
    (re.compile(r"(?:^|\s)-force\b", re.I), "uses -Force"),
]

_SPLIT = re.compile(r"\|\||&&|[|;\n\r{}()]")
_EXPRESSION_START = re.compile(r"""^(?:[$'"\d\-\[@!+,]|\.\w)""")   # an expression ($x, 'a', 5, -gt, .Name), not a command
_DOT_SOURCE = re.compile(r"^\.\s")                              # ". .\script.ps1" runs a script


def why_write(command):
    """The reason this command counts as "write", or "" if it is read-only."""
    text = str(command or "").strip()
    if not text:
        return "no command"
    bare = _HARMLESS_REDIRECT.sub(" ", text)
    for pattern, reason in ALWAYS_WRITE:
        if pattern.search(bare):
            return reason
    for piece in _SPLIT.split(bare):
        piece = piece.rsplit("=", 1)[-1].strip() if "=" in piece else piece.strip()
        if not piece:
            continue
        if _DOT_SOURCE.match(piece):
            return "dot-sources a script"
        if _EXPRESSION_START.match(piece):
            continue
        if not _read_only(piece.split()):
            return f"runs {piece.split()[0]!r}, which isn't on the read-only list"
    return ""


def _read_only(words):
    name, args = words[0].lower(), words[1:]
    if name.endswith(".exe") and name != "where.exe":
        name = name[:-4]
    if name in NOT_READ:
        return False
    if name in READ_ONLY or re.fullmatch(r"(get|format)-\w+", name):
        return True
    if name == "ipconfig":
        return all(a.lower() in IPCONFIG_OK for a in args)
    if name in VERSION_PROGRAMS and len(args) == 1 and args[0] in VERSION_FLAGS:
        return True
    if name == "nvidia-smi":                       # -pl / -ac / -r change the card: only plain or --query looks
        return all(a.startswith(("--query", "--format")) or a in ("-L", "-q") for a in args)
    if name == "git" and args:
        sub, rest = args[0].lower(), args[1:]
        if any(a.startswith(("--output", "--ext-diff")) for a in rest):
            return False
        if sub in GIT_READ:
            return True
        if sub == "remote":
            return all(a in ("-v", "--verbose") for a in rest)
        if sub == "branch":
            return all(a in GIT_BRANCH_FLAGS for a in rest)
        return False
    if name in PROGRAM_READ and args:
        return args[0].lower() in PROGRAM_READ[name]
    return False


def classify(command):
    """'read' if the command provably only looks, else 'write'."""
    return "write" if why_write(command) else "read"


def needs_ok(name, args):
    """A shell tool whose command could change something: it always asks (session.is_risky)."""
    return name in SHELL_TOOLS and classify((args or {}).get("command")) == "write"
