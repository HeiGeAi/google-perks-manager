"""Collect local package notices without including developer profiles or caches."""

from importlib.metadata import distributions
from pathlib import Path
from shutil import copy2
import sys
import sysconfig

output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
for dist in distributions():
    dest = output / f"{dist.metadata['Name']}-{dist.version}"
    for reference in dist.files or ():
        path = Path(reference)
        if not any(part.endswith(".dist-info") for part in path.parts):
            continue
        parts = [part.lower() for part in path.parts]
        if "licenses" not in parts and not path.name.lower().startswith(("license", "copying", "notice")):
            continue
        source = Path(dist.locate_file(reference))
        if source.is_file():
            relative = path.parts[parts.index("licenses") + 1:] if "licenses" in parts else (path.name,)
            target = dest.joinpath(*relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            copy2(source, target)

python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
if python_license.is_file():
    copy2(python_license, output / "Python-LICENSE.txt")
