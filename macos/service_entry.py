"""Executable entry point for the desktop service, Agent CLI and Colab worker."""

import runpy
import sys

from google_manager.server import main


if __name__ == "__main__":
    if sys.argv[1:2] == ["--colab-worker"]:
        del sys.argv[1]
        runpy.run_module("google_manager.colab_worker", run_name="__main__")
    elif sys.argv[1:2] == ["--agent"]:
        from google_manager.agent import main as agent_main
        raise SystemExit(agent_main(sys.argv[2:]))
    else:
        raise SystemExit(main())
