# Characters

Everything about who the assistant is lives here, one folder per character. The code reads the chosen character
(Settings → Nova → "Who should I be?") from these files; to add or change a character, edit or add a folder, and
nothing in the code needs to change.

```
characters/<id>/
  character.json   name, menu label, voice, wake word, look colours, 3D model fit
  persona.md       how she thinks and talks
  model.vrm        her 3D model (a VRoid Studio export, VRM 1.0) - optional; without it she uses the toon body
```

## character.json

| Key | What it is |
|-----|-----------|
| `name` | Her name. Used in the prompt, the chat page, her bubbles and the wake word. |
| `label` | The text in the Settings menu. |
| `voice` | Kokoro voice id (for example `af_bella`, `hf_alpha`). |
| `wake.names` | Spellings of her name the speech recognizer may write (lower case). "Hey <name>" with any of them wakes her. Only the chosen character's names work. |
| `wake.hint` | A sentence that teaches Whisper how to spell her name. |
| `look_preset` | The name `change_look` knows her colours by (`default` for the original look). |
| `look` | Her colours and switches (`nova/avatar/body.py` DEFAULT_LOOK lists the keys). |
| `model.file` | The VRM file in this folder. |
| `model.fit` | How the 3D page fits the toon's moves onto her body: `top` (hair height on screen), `spread` (arms away from her sides), `accessories` (headphones/glasses size), `work` (typing at her desk: arm angles, seat height, desk size). Measured per model with the 3D page's `vrmBones()` helper. |

## persona.md

Three sections: `# Who you are`, `# How you talk`, `# Touch`. `{NAME}` becomes her name. In Nova's file,
`{DESI_TALK}` is replaced by the app's Hindi/English talking rules (they depend on the voice setting).

Makima and Lucy are fan characters for this laptop only: their models must never be shared or uploaded.
