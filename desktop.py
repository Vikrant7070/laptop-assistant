"""Start Nova. Start Nova.bat and the "Start with Windows" shortcut run this file; the app itself is nova/core/desktop.py.

Run:  pythonw desktop.py            (or double-click "Start Nova.bat")
      pythonw desktop.py --show     (open the chat window straight away)
"""
from nova.core.desktop import main

if __name__ == "__main__":
    main()
