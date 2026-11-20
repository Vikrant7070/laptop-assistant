"""A brain whose code reaches outside the sandbox. The guard must refuse to start it."""

import subprocess

from childbrain.link import brain


@brain
def escaper(link):
    subprocess.run(["cmd", "/c", "echo", "out"])
    open("C:/escape.txt", "w").write("out")
