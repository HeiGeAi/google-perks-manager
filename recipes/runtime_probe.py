"""Read the hardware assigned to a running Colab machine."""

import json
import shutil
import subprocess
from pathlib import Path


def runtime_hardware():
    rows = []
    if shutil.which("nvidia-smi"):
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=12, check=False,
        )
        if result.returncode == 0:
            for row in result.stdout.splitlines():
                name, _, memory = row.rpartition(",")
                if name.strip() and memory.strip().isdigit():
                    rows.append({"name": name.strip(), "vram_mib": int(memory)})
    ram = next(int(row.split()[1]) // 1024 for row in Path("/proc/meminfo").read_text().splitlines() if row.startswith("MemTotal:"))
    return {"gpus": rows, "ram_mib": ram, "free_disk_mib": shutil.disk_usage("/content").free // (1024 * 1024)}


if __name__ == "__main__":
    print("GM_HARDWARE=" + json.dumps(runtime_hardware(), ensure_ascii=False))
