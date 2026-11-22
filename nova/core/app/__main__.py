"""`python -m nova.core.app`: the server on its own, without the desktop window."""
import os
import webbrowser

from nova import runtime
from nova.brain import llm
from nova.actions import tools
from nova.core.app import api, background, config, voice_io


def main():
    tools.psutil.cpu_percent(interval=None)  # prime CPU meter
    port = config.PORT
    url = f"http://127.0.0.1:{port}"
    if os.environ.get("NOVA_NO_BROWSER") != "1":
        runtime.spawn("app.open_browser", lambda: webbrowser.open(url), delay=1.2, daemon=False)
    if config.settings()["wake"]:
        voice_io.ears.start()
    background.start_background()
    print(f"\n  {config.ASSISTANT_NAME} is awake at {url}  (brain: {config.brain()}; local: {config.settings()['model']} via Ollama at {llm.HOST})\n")
    api.app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
