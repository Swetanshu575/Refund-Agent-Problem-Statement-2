import os
import sys
import io
import time
from eval_12_questions import run_full_evaluation

README = "README.md"
START_MARKER = "<!-- BENCHMARK_OUTPUT_START -->"
END_MARKER = "<!-- BENCHMARK_OUTPUT_END -->"


class DualWriter:
    # capture stdout while streaming live to console
    def __init__(self, out_stream):
        self.stream = out_stream
        self.buf = io.StringIO()

    def write(self, s):
        self.stream.write(s)
        self.buf.write(s)

    def flush(self):
        self.stream.flush()

    def get_output(self):
        return self.buf.getvalue()


def update_readme_benchmark(output_str):
    if not os.path.exists(README):
        return

    with open(README, "r", encoding="utf-8") as f:
        doc = f.read()

    block = f"{START_MARKER}\n```text\n{output_str.strip()}\n```\n{END_MARKER}"
    if START_MARKER in doc and END_MARKER in doc:
        head, _ = doc.split(START_MARKER, 1)
        _, tail = doc.split(END_MARKER, 1)
        doc = head + block + tail
    else:
        doc = doc.rstrip() + "\n\n## Latest Run\n\n" + block + "\n"

    with open(README, "w", encoding="utf-8") as f:
        f.write(doc)


def main():
    t0 = time.time()
    orig_stdout = sys.stdout
    writer = DualWriter(orig_stdout)
    sys.stdout = writer

    try:
        run_full_evaluation()
    finally:
        sys.stdout = orig_stdout

    captured = writer.get_output()
    update_readme_benchmark(captured)
    
    elapsed = time.time() - t0
    print(f"\n[main.py] Done in {elapsed:.2f}s. Console output automatically saved into {README}.")


if __name__ == "__main__":
    main()

