# Characters

Everything about who the assistant is lives here, one folder per character. The code reads the chosen character
(Settings → Nova → "Who should I be?") from these files; to add or change a character, edit or add a folder, and
nothing in the code needs to change.

```
characters/<id>/
  character.json   name, menu label, voice, wake word, look colours, 3D model fit
  persona.md       how she thinks and talks
  model.vrm        her 3D model (a VRoid Studio export, VRM 1.0) - needed: it is the only way she is drawn
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
| `model.fit` | How the 3D page fits the built-in moves onto her body: `top` (hair height on screen), `floor` (space under her shoes above the bottom of the screen, scene units: 0.35 leaves about 16 px under her toes; default 0.35), `spread` (arms away from her sides), `accessories` (headphones/glasses size), `work` (typing at her desk: arm angles, seat height, desk size). Measured per model with the 3D page's `vrmBones()` helper. |
| `style` | How she moves and reacts - see below. Optional: without it she reacts like Nova. |

### style: her own way of reacting

The code only reads this (`nova/avatar/body.py` `clean_style`); change her behaviour here.

| Key | What it is |
|-----|-----------|
| `size` | How big her talking hands and head moves are (1 = Nova; Makima 0.7, Lucy 1.2). |
| `speed` | How fast she plays reactions (1 = normal). |
| `talk_hands` | Share of plain sentences she says with talking hands (0..1; the rest she says standing still). |
| `faces` | Per feeling (`happy`, `sad`, `surprised`, `angry`), which VRM expressions she shows and how strongly, e.g. `"happy": {"relaxed": 0.9, "happy": 0.15}` for a quiet smile. Expressions: happy, angry, sad, relaxed, surprised. |
| `swap` | Her own version of a reaction: `{"laugh": "quiet_chuckle"}`. Built-in names or her own `moves`. |
| `fidgets` | What she does by herself when idle, with weights: `{"look": 30, "fold_hands": 25}`. |
| `touch` | Clicks on her desktop body: `click` (one click), `right_click` (a list, one at random), `poke` + `pokes` (that many quick clicks within 5 s), `poke_say` (her bubble then), `picked_up` (when dragged), `greeting` (her first bubble). |
| `moves` | Her own moves, in the `new_move` format (`duration`, `keys` with angles such as `right_arm_forward`, `right_elbow`, `head_tilt`, plus `eyes`, `mouth`, `blush`, `symbol`). They play once her live 3D renderer is drawing her; before that she uses the built-in reaction instead. |

A move's arm angle and elbow add up: a forearm pointing straight ahead is roughly `arm_forward + elbow = 90`.
Check a move with the 3D page's `snapshot(move, t)` helper.

## persona.md

Three sections: `# Who you are`, `# How you talk`, `# Touch`. `{NAME}` becomes her name. In Nova's file,
`{DESI_TALK}` is replaced by the app's Hindi/English talking rules (they depend on the voice setting).

Makima and Lucy are fan characters for this laptop only: their models must never be shared or uploaded.
