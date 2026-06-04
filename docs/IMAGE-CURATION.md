# Image curation — Instagram @gluck_bags

> Document produced during the file-by-file review of the material downloaded from the public
> Instagram profile **@gluck_bags** (posts, reels and highlights) to select the images
> suitable for the website (Flask).

## Source of the material
- **Full download:** [`ig-gluck_bags/`](../ig-gluck_bags/) — 51 feed photos + 12 feed videos + 32 highlight files + 7 highlight videos.
- **Tool:** `gallery-dl` with Chrome cookies (Default profile).
- **Active stories:** 0 at the time of download (they last 24 h).

## Result: selected for web → [`app/static/img/`](../app/static/img/)
14 images, optimized (sRGB, progressive JPEG q82, no metadata, ~2.2 MB total).

| Folder | Use |
|---|---|
| `hero/` | Cover / aspirational lifestyle (real photography) |
| `productos/` | Clean product shots on the brand's studio set |
| `banners/` | Pre-designed category plates (serif title) |
| `marca/` | Logo graphics + `avatar-perfil-gluck.jpg` (account profile picture, 736×736 — the account uses the logo as its avatar; serves as favicon/avatar in the header) |

### Detected visual identity
The brand's recurring studio set: **exposed brick wall + white cushions + framed line-art
prints**. The best product shots use this background. The plates with a serif title
(TOTE, MINI BAG, BUCKET BAG, Sobre) are already-designed marketing pieces → they serve as banners.

---

## ✅ SELECTED (14)
Reviewed at full resolution.

| File | Dimensions | Category | Note |
|---|---|---|---|
| `2988322762687362350.jpg` | 1080×1228 | Brand | Scattered b/w GLÜCK logo. Ideal header/footer/about. → logo-gluck.jpg |
| `2995521251800996563.jpg` | 1080×1080 | Hero | Cognac tote on the beach, natural light. The best of all. → hero-tote-cognac-playa.jpg |
| `2997550906410143664.jpg` | 1080×1220 | Lifestyle | Editorial at a museum with a cognac tote. ⚠ Picasso artwork in the background (third party). → lifestyle-tote-cognac-museo.jpg |
| `3000538910292552769.jpg` | 1080×1080 | Hero | Grey felt tote on a sailboat deck. Premium texture. → hero-tote-gris-velero.jpg |
| `3058507357800937340.jpg` | 1080×1920 | Banner | Designed BUCKET BAG plate (cognac). → banner-bucket-bag.jpg |
| `3061388796053846474.jpg` | 1080×1920 | Banner | Designed green MINI BAG plate. → banner-mini-bag-verde.jpg |
| `3061390609628834361.jpg` | 1080×1920 | Banner | Designed pink MINI BAG plate. → banner-mini-bag-rosa.jpg |
| `3062191636883989708.jpg` | 1080×1920 | Product | Cognac tote front view, studio set (with 'TOTE' label). → tote-cognac-02.jpg |
| `3062191905235570127.jpg` | 1080×1920 | Product | Cognac tote 3/4, the cleanest one (no text). → tote-cognac-01.jpg |
| `3066441858019678558.jpg` | 1080×1920 | Product | Pink 'Sobre' clutch on the studio set (with label). → clutch-rosa-sobre.jpg |
| `3066448434470068790.jpg` | 1080×1920 | Product | Pink 'Bandolera tira larga' crossbody (with label). → crossbody-rosa-bandolera.jpg |
| `3066472608014075543.jpg` | 1080×1080 | Product | Grey tote top-down showing the interior/capacity. → tote-gris-interior.jpg |
| `3075084120480493078.jpg` | 1080×1080 | Hero | Black crossbody in front of the Milan Duomo. Travel lifestyle. → hero-crossbody-negra-duomo.jpg |
| `3075970400218642624_3075970396133446018.jpg` | 1080×1080 | Product | Clean pink crossbody on linen. → crossbody-rosa.jpg |

## 🟡 SECOND TIER (14)
Valid product on the studio set; they didn't make the cut to avoid repeating variants, but they are good replacements.

| File | Dimensions | Category | Note |
|---|---|---|---|
| `2985968641133525736.jpg` | 1080×1002 | Product | Cognac tote on a table with a counter. Correct, somewhat flat background. |
| `3010058593509239532.jpg` | 1440×2560 | Brand | Brand card flatlay. Works as secondary branding. |
| `3020187770442894734.jpg` | 1080×1920 | Brand | Minimal 'MINI BAG' card. Secondary branding. |
| `3061391385306700196.jpg` | 1080×1920 | Product | Mini bag (variant) on the studio set. Good replacement. |
| `3061392123101539332.jpg` | 1080×1920 | Product | Mini bag on the studio set. |
| `3061392393198025994.jpg` | 1080×1920 | Product | Mini bag on the studio set. |
| `3062192186916807709.jpg` | 1080×1920 | Product | Cognac tote, alternative angle. |
| `3062192362398011765.jpg` | 1080×1920 | Product | Cognac tote, alternative angle. |
| `3064251133689359953.jpg` | 1080×1920 | Product | Product on the studio set. |
| `3066439828983168906.jpg` | 1080×1920 | Product | Product on the studio set. |
| `3066450375359219463.jpg` | 1080×1920 | Product | Cognac clutch on the studio set. |
| `3066451009059877885.jpg` | 1080×1920 | Product | Cognac clutch on the studio set. |
| `3066451386622667407.jpg` | 1080×1920 | Product | Cognac clutch on the studio set. |
| `3066452137763829809.jpg` | 1080×1920 | Product | Green clutch on the studio set. |

## ❌ DISCARDED (23)
Reasons: story screenshots with Instagram UI, moodboards/interiors without product (inspiration
reposts with rights risk), low resolution (<800px), duplicates with overlaid text,
or product not the main subject.

| File | Dimensions | Category | Note |
|---|---|---|---|
| `2898544321004392786.jpg` | 736×736 | Brand | Black GLÜCK logo plate. Low resolution (736px). |
| `2997529383422449838.jpg` | 736×736 | Lifestyle | Minimal sand/beach. Low resolution (736px), product barely visible. |
| `3000454925747025186.jpg` | 735×735 | Moodboard | Minimalist interior/arch, no product. Low res. |
| `3000455558013263998.jpg` | 1440×2560 | Moodboard | White-cube gallery, no product (inspiration repost). |
| `3001326380483541603.jpg` | 736×736 | Text | Typographic 'CHANGE...' poster. Low res, no product. |
| `3007126416148408966.jpg` | 1080×1080 | Moodboard | Abstract spiral staircase, no product. |
| `3010020330962575959.jpg` | 735×735 | Lifestyle | Person reading a magazine. Low res, product not the main subject. |
| `3010044936620410957.jpg` | 1080×1080 | Flatlay | Flatlay over a brown monogram (possible third-party print). |
| `3010770035212288942.jpg` | 564×564 | Product | 564×564, too low resolution. |
| `3018770322895810391.jpg` | 1080×1080 | Moodboard | Façade with arches, no product. |
| `3018772191919719256.jpg` | 1440×2560 | Lifestyle | Person sitting with a magazine; product not the main subject. |
| `3019265976240199441.jpg` | 794×794 | Flatlay | Flatlay with glasses over a monogram. Low res. |
| `3035445540896866139.jpg` | 1080×1920 | Story | Black bag on a waterfall BUT it's a story screenshot with Instagram UI and a third-party repost. |
| `3036842679581891774.jpg` | 1080×1080 | Moodboard | Arches with a figure, architecture without product. |
| `3041847963046858878.jpg` | 1080×1920 | Artistic | Shadow of a hand with glasses. No clear product. |
| `3058509238929490476.jpg` | 1080×1920 | Minimal | Beige wall with a small object. Product barely visible. |
| `3066467620642678893.jpg` | 735×735 | Product | 735×735, low resolution. |
| `3069262435244251147.jpg` | 1080×1080 | Moodboard | Beige interior (bedroom), no product (repost). |
| `3069422914188522320_3069422909381890250.jpg` | 1080×1080 | Lifestyle | Glasses, no bag. |
| `3069422914188522320_3069422909507517874.jpg` | 1080×1080 | Lifestyle | Glasses, no bag. |
| `3072379842581638381_3072379838722791103.jpg` | 1080×1080 | Moodboard | Gallery interior, no product. |
| `3072379842581638381_3072379838722903446.jpg` | 736×736 | Lifestyle | Museum with overlaid '@gluck_bags' text. Duplicate-with-text of the clean 2997550... |
| `3075970400218642624_3075970396133322968.jpg` | 608×608 | Product | 608×608, low res. Duplicate of the clean version. |

---

## 🎬 Videos — reviewed one by one
19 files downloaded (12 from feed + 7 from highlights) → **12 unique videos** (several were duplicated
between feed and highlights). All vertical 720×1280 (story format). Each one was reviewed by extracting
4 frames (start/middle/end) to judge the motion, not just a single frame.

### ✅ Selected videos (5) → [`app/static/video/`](../app/static/video/)
Transcoded to web MP4 (H.264, `yuv420p`, CRF 26, `+faststart`). Posters in
[`app/static/img/video-posters/`](../app/static/img/video-posters/). Total ~6.3 MB.

| Web file | Source | Dur. | Suggested use |
|---|---|---|---|
| `relanzamiento-gluck-2026.mp4` | `3849969156360231774` | 10s | ⭐ Hero/intro. "GLÜCK 2026", metallic crossbody on a marble pedestal (Versailles style), "Shop now". The newest and most polished. |
| `crossbody-rosa-movimiento.mp4` | `3076675177399273511` | 12s | Product loop: pink crossbody swinging against a colored wall. Dynamic. |
| `bucket-bag-reveal.mp4` | `3068693524286560220` | 6s | Product reveal: cognac bucket bag on a dark background. Clean. |
| `cuero-loop-desktop.mp4` / `cuero-loop-mobile.mp4` | `3070829165132832638` | 10s | Behind the scenes: leather rolls at the supplier, a hand running over the colors. Full-bleed `materia` section. **AI-upscaled** (see below). |
| `packaging-desktop.mp4` / `packaging-mobile.mp4` | `3067239620940361611` | 11s | Unboxing: green clutch + GLÜCK kraft packaging. Shipping/packaging section. **AI-upscaled** (see below). |

### ❌ Discarded videos (7)
| Source | Dur. | Reason |
|---|---|---|
| `3036816700303253370` | 15s | "COMING SOON" plate over a bucket bag (expired promo). |
| `3041845635988097233` | 15s | Bucket bag reveal in the studio; redundant with `bucket-bag-reveal`. |
| `3041847197963801926` | 4s | Pan of the set without a main product. |
| `3064915475236176714` | 3s | Very short rotation of mini bags. |
| `3065709370297082716` | 15s | Display of green/cognac mini bags; correct but redundant. |
| `3066446754735413382` | 19s | "MINI BAGS" promo plate + flatlay. |
| `3067252364787800816` | 11s | Same unboxing as the selected one but with a `@gluck_bags` watermark. |

## 🔬 AI upscaling (Real-ESRGAN)
Both source videos were only **720×1280** and looked pixelated on desktop. Common pipeline: extract every
frame from the original → **×4 upscale with Real-ESRGAN** (`realesr-animevideov3-x4`, video model — good
temporal consistency, ~13s/frame on an Intel Iris GPU) → 2880×5120 per frame → supersampled
downscale/crop to the target sizes → reassemble at 30 fps. Compact 2880×5120 masters kept in
[`ig-gluck_bags/`](../ig-gluck_bags/) (`*-MASTER-2880x5120.mp4`) for future re-crops; the bulky PNG frames are deleted.

### Packaging (`.closer-media`, vertical)
On Retina (DPR 2) the box shows at **686×1227 CSS = 1371×2453 physical px**. 321 frames upscaled, then:
| Variant | Resolution | Weight (mp4 / webm) | Loads when |
|---|---|---|---|
| `packaging-desktop.mp4` / `.webm` | 2160×3840 | 6.0 / 4.7 MB | viewport ≥ 760px |
| `packaging-mobile.mp4` / `.webm` | 1080×1920 | 1.3 / 1.8 MB | viewport < 760px |

### Materia / leather (`.materia-video`, full-bleed — desktop landscape, mobile vertical)
The desktop loop is a **landscape band crop** of the vertical source (by design); the previous file was a
720px stretch. Reverse-engineered: loop = first 10s (300 frames), desktop crop = center band 720×404 at
y=436. From the ×4 frames the desktop crop is taken **natively** (`crop=2880:1616:0:1744` → 2880×1616, no stretch).
| Variant | Resolution | Weight (mp4 / webm) | Loads when |
|---|---|---|---|
| `cuero-loop-desktop.mp4` / `.webm` | 2880×1616 (landscape) | 7.1 / 5.2 MB | viewport ≥ 760px |
| `cuero-loop-mobile.mp4` / `.webm` | 1080×1920 (vertical) | 3.7 / 3.8 MB | viewport < 760px |

Both use the `data-desktop-*`/`data-mobile-*` pattern; the JS picks the variant by viewport. Verified in the
browser: on-screen sizes intact, intrinsic resolution now matches the physical pixels needed on Retina.

## ✅ Applied optimizations
- **Hero loops** (muted, trimmed ≤10s) → [`app/static/video/loops/`](../app/static/video/loops/): `relanzamiento-loop`, `crossbody-rosa-loop`, `bucket-bag-loop`, `cuero-loop` (mp4 + webm).
- **Square thumbnails** 600×600 (center-crop) for the grid → [`app/static/img/thumbs/`](../app/static/img/thumbs/) (13).
- **`.webp`** of the 33 images (~30% lighter than the jpg) — they coexist with the `.jpg` for `<picture>`.
- **`.webm`** (VP9) of the 9 videos — they coexist with the `.mp4` for `<source>` with fallback.

### How to use them in the templates (recommended pattern)
```html
<!-- image with webp + jpg fallback -->
<picture>
  <source srcset="{{ url_for('static', filename='img/productos/tote-cognac-01.webp') }}" type="image/webp">
  <img src="{{ url_for('static', filename='img/productos/tote-cognac-01.jpg') }}" alt="Cognac tote">
</picture>

<!-- muted hero loop with autoplay -->
<video autoplay muted loop playsinline poster="{{ url_for('static', filename='img/video-posters/relanzamiento-gluck-2026.jpg') }}">
  <source src="{{ url_for('static', filename='video/loops/relanzamiento-loop.webm') }}" type="video/webm">
  <source src="{{ url_for('static', filename='video/loops/relanzamiento-loop.mp4') }}" type="video/mp4">
</video>
```

## Final asset inventory (`app/static/`)
- `img/`: 33 jpg + 33 webp (hero, productos, banners, marca, thumbs, video-posters) — 5.6 MB
- `video/`: 9 mp4 + 9 webm (5 main + 4 loops) — 18 MB
</content>
