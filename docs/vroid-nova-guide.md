# Making Nova in VRoid Studio

A checklist to follow with VRoid Studio open beside it. It takes about 30–60 minutes.
Colours come from Nova's current look (`data/look.json` and `static/nova3d.html`), so the new model matches her.

## 0. Install

- Get **VRoid Studio** (free) from https://vroid.com/en/studio or on Steam ("VRoid Studio v2").
- Open it, click **Create New**, and pick the **feminine** base.

## 1. Body (Body tab)

Nova is small and cute. Keep the proportions slightly chibi without overdoing it, because the model is also used for full-body moves.

- **Height:** about 85–90% of the default.
- **Head size:** a little bigger than default, around +10–15%. Don't go full chibi: arms have to clear the head when she waves.
- **Skin colour:** `#FFE2D4` (light peach).

## 2. Face (Face tab)

- **Eyes:** big, round and friendly. Raise eye size a little and use a soft upper eyelash.
- **Iris colour:** `#7C5CFF` (violet-blue). If there's an iris highlight colour, use `#5FE3F5` (cyan).
- **Mouth:** a small, default smile.
- **Blush:** light pink, low strength.
- In the **Expression** sub-tab, leave the presets (Joy, Angry, Sorrow, Fun, Surprised) and the mouth shapes (A, I, U, E, O) **on**. Nova's lip-sync and moods use them.

## 3. Hair (Hairstyle tab)

Nova's hair is **long with side locks, bangs, and one antenna strand on top (ahoge)**.

- Start from a **long straight preset** with bangs.
- **Colour:** `#6B4FE0` (purple). Use `#4A33B0` (darker purple) for shadows and the inner layer.
- **Tips:** if the preset has a gradient or tip colour, set the ends to `#45D4EF` (cyan). This is Nova's signature look.
- **Ahoge:** in the hair editor, add one **curved strand** on top of the head, slightly to her right.
- **Hair physics:** keep the default "swaying" groups switched on (Hair → Swaying). They become spring bones, which make the hair move by itself in Nova.

> The look currently says `"hair": "green"` because you changed it with `change_look`. Purple is her default. Use whichever colour you want as the base: the app can recolour her later.

## 4. Outfit (Outfit tab)

- **Top:** a **hoodie or parka** if one is listed, otherwise a simple long-sleeve top. Colour `#2C3266` (navy), cuffs and hem `#1E2247`.
- **Bottom:** leggings or slim pants, colour `#252A4D`.
- **Shoes:** sneakers, colour `#F1F2FF` (near-white).
- A **yellow star `#FFD25E` on the chest** is optional. You can draw it with the texture editor's brush. If you skip it, I'll add it in code.

## 5. Things to skip (I'll add them in code)

VRoid has no headphones that match Nova's, so I'll attach these to the model's head bone in three.js. Most of these meshes already exist in `nova3d.html`:

- headphones with the glowing cyan rings, and the listening ripples
- the star hair clip
- glasses, cat ears, beanie and bow (the `change_look` extras)
- the desk, laptop and mug used in the "working" pose

## 5b. Calm "office" look (Makima-style). Use this instead of sections 3–5.

This is an inspired-by look built from VRoid's own parts. It's a **fan model for private use on your laptop only**:
don't upload or share it (on VRoid Hub, for example), because the character design belongs to its creators.

- **Hair:** long, colour `#D9776A` (salmon red), darker `#B5574D` for shadows. Use **bangs parted slightly off-centre**
  and side locks framing the face, with the rest gathered into **one long braid down the back** (Hairstyle → Braid
  in the hair editor, or a braid preset). No ahoge.
- **Eyes:** a calm, slightly narrow shape with a soft lower lid. Turn the eye size down a little from Nova's big eyes.
  Iris: **Face → Eye → Iris → Edit Texture**, add a layer and import
  `docs\ringed-iris.png` (golden concentric rings). Scale it to fit the iris, then turn **iris highlight** off or down.
- **Expression:** keep the default faint smile. The Joy/Angry/Sorrow/Fun/Surprised shapes still matter.
- **Outfit:**
  - **Top:** white collared shirt `#F4F4F2`, tucked in.
  - **Tie:** a thin black necktie `#1A1A1A`. If there's no tie preset, paint one on the shirt with the texture brush.
  - **Bottom:** dark slacks `#22242B`. **Shoes:** black `#151515`.
  - **Coat (optional):** a long dark coat `#2A2D33` if you want it.
- **Skip:** headphones, star clip, cat ears and the other cute extras. I'll leave Nova's code accessories off for this
  look. She'll still show listening with soft ripples near her ears.

## 5c. Bright "celestial" look (Lucy-style). A separate model, so a separate VRoid project.

Like 5b, this is a **fan model for private use only**: don't upload or share it.

- **Body:** default height or a touch shorter, normal head size, cheerful proportions.
- **Hair:** shoulder-to-mid-back length, colour `#F7D774` (warm blonde), lighter `#FFF3B0` for highlights.
  Use **side bangs**, with a **small side ponytail tied on her left**: add a short tied-up strand group in the hair
  editor. For the **ribbon** on the ponytail, use a blue `#2563EB` hair accessory or bow if VRoid has one; if not, skip
  it and I'll add Nova's bow in code.
- **Eyes:** big, bright and round. Iris `#8A5A2B` (warm brown), with a normal highlight on.
- **Expression:** a happy default smile.
- **Outfit:**
  - **Top:** a white sleeveless or short-sleeve top `#F8FAFC` with a blue `#2563EB` trim or cross design. Paint it
    with the texture brush if you like.
  - **Bottom:** a short blue skirt `#2563EB`. **Belt (optional):** brown `#5A3A22`, with a little gold `#F5C542`
    key-ring charm painted on.
  - **Boots:** brown `#3B2A1A`.
- **Skip:** headphones and the ahoge.

## Which file is which

Each character has her own model. Export each one with section 6 and use these exact names in `static\nova3d\`:

| Character | VRoid look | File name |
|-----------|-----------|-----------|
| Nova (original) | sections 1–5 | `nova.vrm` |
| Makima | section 5b | `makima.vrm` |
| Lucy | section 5c | `lucy.vrm` |

Start each one with **File → New** in VRoid and save each `.vroid` project separately.

## 6. Export (the important part)

Use the **Export** button (top right) → **Export as VRM**.

1. **VRM version:** choose **VRM 1.0**.
2. **Reduction settings.** The model runs on the laptop's Intel graphics chip, so keep it light:
   - **Polygon reduction:** on, aiming for **under ~30,000 triangles**. Keep hair reduction mild so the hair doesn't look jagged.
   - **Material reduction:** on, **≤ 8 materials**, texture atlas **2048 × 2048**.
   - **Bone reduction:** off for now, so the fingers stay.
   - Keep **"delete transparent meshes"** on.
3. **Metadata:** name **Nova**, author **you**, and allow use by **"Only Author"**. It's your private character.
4. Save it as:

   ```
   C:\Users\vikra\OneDrive\Desktop\Laptop assistant\static\nova3d\nova.vrm
   ```

5. Also save the project (`.vroid` file) somewhere safe, for example `Documents\Nova\nova.vroid`, so you can edit her later.

## 7. Then tell Claude "nova.vrm is ready"

What happens next:

- Load the VRM in `nova3d.html` with `@pixiv/three-vrm`, and keep the current body as a fallback.
- Map the joint angles in `body.py` and `moves.json` onto the VRM skeleton, so existing moves keep working.
- Drive her mouth (A/I/U/E/O shapes) from the voice loudness, add blinking, glances, eyes that follow the cursor, and the mood expressions.
- Point `change_look` colours at the VRM materials.
- Measure CPU use against today's ~27% of one core, and send you before/after screenshots.
