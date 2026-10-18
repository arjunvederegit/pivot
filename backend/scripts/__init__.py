"""Backend command-line scripts, run as modules from `backend/`: `python -m scripts.<name>`.

This file makes `scripts` a package. Running `python -m scripts.eval_rag` from `backend/`
puts `backend/` (the current directory) on `sys.path`, so `import app` resolves to the
source tree without relying on an editable install.
"""
