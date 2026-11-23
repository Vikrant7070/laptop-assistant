"""Finding, listing, reading, writing, moving and cleaning up files."""


import os
import shutil
import time
from pathlib import Path

from nova.log import note
from nova.actions.registry import tool
from nova.shared import ps_escape
from nova.actions.tools import common


@tool("List a folder's contents, newest first.", friendly="Looked inside a folder", props={"path": common.S},
      required=["path"])
def list_folder(path="downloads"):
    p = common._resolve(path)
    if not p.is_dir():
        return f"{p} is not a folder."
    entries = []
    for i in p.iterdir():
        try:
            st = i.stat()
            entries.append((st.st_mtime, i, st.st_size, i.is_dir()))
        except OSError:
            entries.append((0, i, 0, False))     # a locked or broken entry is still listed
    entries.sort(key=lambda e: e[0], reverse=True)
    rows = [f"{'[DIR] ' if d else ''}{i.name}{'' if d else ' - ' + common._fmt_bytes(size)}" for _, i, size, d in entries[:60]]
    return f"{p} (newest first):\n" + "\n".join(rows)


@tool("Find files/folders whose name contains text.", friendly="Searched files",
      hints="find my file, where is the document", props={"name_contains": common.S, "folder": common.S},
      required=["name_contains"])
def search_files(name_contains, folder="home", max_results=25):
    root = common._resolve(folder)
    needle = name_contains.lower()
    hits, start = [], time.time()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("AppData", "node_modules", "$Recycle.Bin")]
        for f in filenames + dirnames:
            if needle in f.lower():
                hits.append(os.path.join(dirpath, f))
                if len(hits) >= int(max_results):
                    return "\n".join(hits)
        if time.time() - start > 20:
            hits.append("(stopped searching after 20 seconds)")
            break
    return "\n".join(hits) or f"Nothing named like '{name_contains}' in {root}."


@tool("Find big files that take up space.", friendly="Hunted big files", hints="free up space, disk full, big files",
      props={"folder": common.S, "min_mb": common.N}, required=[])
def find_large_files(folder="home", min_mb=200):
    root = common._resolve(folder)
    big, start = [], time.time()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "$Recycle.Bin"]
        for f in filenames:
            try:
                fp = os.path.join(dirpath, f)
                size = os.path.getsize(fp)
                if size >= float(min_mb) * 1024 * 1024:
                    big.append((size, fp))
            except OSError:
                pass
        if time.time() - start > 25:
            break
    big.sort(reverse=True)
    return "\n".join(f"{common._fmt_bytes(s)}  {p}" for s, p in big[:30]) or f"No files over {min_mb} MB in {root}."


CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".svg"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".odt", ".md"},
    "Videos": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "Music": {".mp3", ".wav", ".flac", ".m4a", ".aac"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Installers": {".exe", ".msi"},
    "Code": {".py", ".js", ".html", ".css", ".json", ".ipynb", ".java", ".cpp", ".c"},
}


def _organize_plan(folder):
    p = common._resolve(folder)
    plan = []
    for f in p.iterdir():
        if f.is_file():
            cat = next((c for c, exts in CATEGORIES.items() if f.suffix.lower() in exts), "Other")
            plan.append((f, p / cat / f.name))
    return p, plan


@tool("Show how a folder's loose files would be sorted into category folders (no changes).",
      friendly="Planned a cleanup", props={"folder": common.S}, required=[])
def preview_organize_folder(folder="downloads"):
    p, plan = _organize_plan(folder)
    if not plan:
        return f"No loose files in {p}."
    counts = {}
    for _, dest in plan:
        counts[dest.parent.name] = counts.get(dest.parent.name, 0) + 1
    return f"{len(plan)} files in {p} would be sorted into: " + ", ".join(f"{k} ({v})" for k, v in counts.items())


@tool("Sort a folder's loose files into Images/Documents/Videos/... subfolders.", friendly="Organized files",
      risky="Move files into category folders", props={"folder": common.S}, required=[])
def organize_folder(folder="downloads"):
    p, plan = _organize_plan(folder)
    moved = 0
    for src, dest in plan:
        dest.parent.mkdir(exist_ok=True)
        final = dest
        n = 1
        while final.exists():
            final = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
            n += 1
        try:
            shutil.move(str(src), str(final))
            moved += 1
        except OSError:
            pass
    return f"Moved {moved} files in {p} into category folders."


@tool("Create a folder.", friendly="Created a folder", props={"path": common.S}, required=["path"])
def create_folder(path):
    p = common._resolve(path)
    if p.is_dir():          # "Created" for a folder that was already there hid a wrong path (task exam 2026-11-23)
        return f"Nothing created: {p} was already there. If you meant a new folder inside it, give its full path."
    p.mkdir(parents=True, exist_ok=True)
    return f"Created {p}"


@tool("Move a file or folder to the Recycle Bin.", friendly="Moved to Recycle Bin",
      risky="Move something to the Recycle Bin", props={"path": common.S}, required=["path"])
def delete_to_recycle_bin(path):
    p = common._resolve(path)
    if not p.exists():
        return f"{p} does not exist."
    kind = "DeleteDirectory" if p.is_dir() else "DeleteFile"
    cmd = (f"Add-Type -AssemblyName Microsoft.VisualBasic; "
           f"[Microsoft.VisualBasic.FileIO.FileSystem]::{kind}('{ps_escape(p)}', 'OnlyErrorDialogs', 'SendToRecycleBin')")
    out = common._ps(cmd)
    return f"Moved {p} to the Recycle Bin. {out if 'ERR' in out else ''}".strip()


@tool("How much space temporary files use.", friendly="Measured temp files", props={}, required=[])
def temp_files_size():
    total = 0
    for root in {os.environ.get("TEMP", ""), r"C:\Windows\Temp"}:
        for dirpath, _, files in os.walk(root):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
    return f"Temporary files take about {common._fmt_bytes(total)}."


@tool("Delete old temporary files to free space.", friendly="Cleaned temp files", risky="Delete old temp files",
      hints="clean up junk temp files, free space", props={}, required=[])
def clean_temp_files():
    root = os.environ.get("TEMP", "").strip()
    # Without a real TEMP folder, Path("") would be the current folder - never clean that (or a drive or home).
    if not root or not os.path.isabs(root) or not Path(root).is_dir() or Path(root) in (Path(Path(root).anchor), common.HOME):
        return "I couldn't find the temporary-files folder (TEMP), so I didn't delete anything."
    freed, removed, recent, skipped, first_err = 0, 0, 0, 0, ""
    for entry in Path(root).iterdir():
        try:
            size = entry.stat().st_size if entry.is_file() else sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
            if time.time() - entry.stat().st_mtime < 3600:
                recent += 1
                continue  # skip files in use / very recent
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
            freed += size
            removed += 1
        except Exception as e:  # intentional: files in use can't be deleted; counted and logged below
            skipped += 1
            first_err = first_err or f"{entry.name}: {type(e).__name__}: {e}"
    note(f"CLEAN TEMP {root}: removed {removed} items, freed {common._fmt_bytes(freed)}, kept {recent} recent, "
         f"skipped {skipped} in use" + (f" (first: {first_err[:200]})" if first_err else ""))
    return f"Cleaned temporary files and freed about {common._fmt_bytes(freed)}. Files in use were skipped."


@tool("Permanently empty the Recycle Bin.", friendly="Emptied Recycle Bin",
      risky="Permanently empty the Recycle Bin", dangerous=True, props={}, required=[])
def empty_recycle_bin():
    return common._ps("Clear-RecycleBin -Force -ErrorAction SilentlyContinue; 'Recycle Bin emptied.'")


@tool("Read a text file (txt, md, csv, code, logs).", friendly="Read a file", props={"path": common.S, "max_chars": common.N},
      required=["path"])
def read_text_file(path, max_chars=6000):
    p = common._resolve(path)
    if not p.is_file():
        return f"{p} is not a file."
    return p.read_text(encoding="utf-8", errors="replace")[: int(max_chars)]


@tool("Create/overwrite or append to a text file.", friendly="Wrote a file", risky="Write a file",
      props={"path": common.S, "content": common.S, "append": common.B}, required=["path", "content"])
def write_text_file(path, content, append=False):
    p = common._resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    return f"{'Appended to' if append else 'Wrote'} {p}"


@tool("Move or rename a file or folder.", friendly="Moved/renamed", risky="Move/rename",
      props={"source": common.S, "destination": common.S}, required=["source", "destination"])
def move_or_rename(source, destination):
    src, dst = common._resolve(source), common._resolve(destination)
    if not src.exists():
        return f"{src} does not exist."
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        return f"{dst} already exists - pick another name."
    shutil.move(str(src), str(dst))
    return f"Moved {src} -> {dst}"


@tool("Copy a file or folder.", friendly="Copied files", props={"source": common.S, "destination": common.S},
      required=["source", "destination"])
def copy_path(source, destination):
    src, dst = common._resolve(source), common._resolve(destination)
    if not src.exists():
        return f"{src} does not exist."
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        return f"{dst} already exists - pick another name."
    (shutil.copytree if src.is_dir() else shutil.copy2)(str(src), str(dst))
    return f"Copied to {dst}"
