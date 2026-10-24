import os
import sys
import io
import time
from eval_12_questions import run_full_evaluation

# quick config
README_FILE = "README.md"
START_TAG = "<!-- BENCHMARK_OUTPUT_START -->"
END_TAG = "<!-- BENCHMARK_OUTPUT_END -->"


class Tee(object):
    """small helper to print to terminal and capture to buffer at the same time"""
    def __init__(self, stream):
        self.stream = stream
        self.mem = io.StringIO()

    def write(self, data):
        self.stream.write(data)
        self.mem.write(data)

    def flush(self):
        self.stream.flush()

    def value(self):
        return self.mem.getvalue()


def sync_readme(new_output: str):
    if not os.path.exists(README_FILE):
        print(f"warn: {README_FILE} not found, skipping sync")
        return

    with open(README_FILE, "r", encoding="utf-8") as fp:
        content = fp.read()

    formatted_block = f"{START_TAG}\n```text\n{new_output.strip()}\n```\n{END_TAG}"

    if START_TAG in content and END_TAG in content:
        head = content.split(START_TAG)[0]
        tail = content.split(END_TAG)[1]
        updated = head + formatted_block + tail
    else:
        # if someone accidentally deleted the tags, just stick it at the bottom
        updated = content + "\n\n## Latest Console Output\n\n" + formatted_block

    with open(README_FILE, "w", encoding="utf-8") as fp:
        fp.write(updated)


def main():
    start = time.time()
    
    # redirect stdout to our tee logger so we see progress live
    orig_stdout = sys.stdout
    tee = Tee(orig_stdout)
    sys.stdout = tee

    try:
        run_full_evaluation()
    finally:
        sys.stdout = orig_stdout

    captured = tee.value()
    sync_readme(captured)
    
    elapsed = round(time.time() - start, 2)
    print(f"\n[main.py] Done in {elapsed}s. Console output automatically saved into {README_FILE}.")


if __name__ == "__main__":
    main()
