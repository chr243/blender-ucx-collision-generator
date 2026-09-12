# UCX Collision Generator

Blender **4.3.2+** add-on that auto-generates **UCX** convex collision meshes for **Unreal Engine 5** FBX export.

Built mainly for rocks, cliffs, and terrain — instead of one AABB it creates multiple convex hulls that follow the shape.

If you like it let me know via https://buymeacoffee.com/chr2436 ;)

---

## Install

1. `Edit → Preferences → Add-ons → Install…`
2. Select `ucx_collision_generator.py` **only** (single file, not the whole repo folder)
3. Enable **UCX Collision Generator**
4. Open the **3D Viewport** sidebar (`N`) → tab **UCX**

Raw download:  
https://github.com/chr243/blender-ucx-collision-generator/raw/main/ucx_collision_generator.py

---

## Usage

1. Select one or more **Mesh** objects (e.g. `SM_Cliff_01`).
2. Pick a preset (**Low / Medium / High**) or tweak parameters.
3. Click **Generate UCX Collisions**.
4. Objects appear as: `UCX_SM_Cliff_01_00`, `UCX_SM_Cliff_01_01`, …
5. **Delete UCX** removes collisions matching the selected sources.
6. **Regenerate UCX** = Delete + Generate.

The source mesh is **never modified**. UCX objects copy the source `matrix_world`; hull geometry stays in source local space — correct alignment even with unapplied scale/rotation.

### FBX export to UE5

- Keep naming `UCX_<StaticMeshName>_XX` (zero-padded from `_00`).
- UE5 recognizes `UCX_*` objects as convex collision on FBX import.
- Export the source together with its UCX objects (or the `UCX_<name>` collection).

---

## Parameters

| Parameter | Description |
|-----------|-------------|
| **Max Convex Hulls** | Max collision pieces per object |
| **Target Accuracy** | Split aggressiveness (higher = tighter fit) |
| **Min Hull Size** | Drop hulls whose bbox diagonal is below threshold |
| **Geometry Simplification** | Decimate ratio on the working copy (lower = faster) |
| **Max Verts Per Hull** | Vertex cap per hull (`0` = unlimited) |
| **Remove Tiny Fragments** | Drop tiny clusters before hulling |
| **Collision Margin** | Expand hulls from centroid (local units) |
| **Create UCX Collection** | Put results in collection `UCX_<source_name>` |
| **Preserve Cavities** | Avoid clogging tunnels/arches (auto through-bore detection) |

### Presets

- **Low** — 12 hulls, heavy simplification (fast / cheap)
- **Medium** — 18 hulls, balanced
- **High** — 28 hulls, finer splits

All presets enable **Preserve Cavities** by default. You can still tweak values after picking a preset.

---

## Algorithm

Blender has no built-in V-HACD. This add-on (v1.2+) uses **BFS AABB median-split on a vertex cloud** + `convex_hull`:

1. Evaluated mesh via depsgraph (source modifiers are not applied destructively).
2. Temporary `bmesh` in **source local space** + decimate.
3. Balanced BFS vertex partition up to hull budget / accuracy.
4. Per part: clean `bmesh.ops.convex_hull` (strip `geom_interior`), light expand, vertex limit.
5. New objects with `matrix_world = source.matrix_world`, display `WIRE`.
6. Cleanup of temporaries.

### Preserve Cavities (v1.3)

When enabled, the add-on detects a **through tunnel/arch** (exterior air through the mass center + open ends with local walls) and then:

- raises the hull budget,
- splits cavity-heavy AABB clusters,
- assigns vertices by seed AABB (so opposite tunnel walls stay separate),
- clears bore plugs (point-in-convex tests on centerline samples).

Open cliffs / solid rocks usually **do not** enter this mode (ray filters + center-to-AABB distance ratio).

Headless tests on Blender **4.3.2** (v1.3):

- Poly Haven `rock_07`, `boulder_01`, `namaqualand_cliff_01/02`, `rock_face_02` (CC0) — solids
- Synthetic arch/tunnel (`SM_RockArch_Tunnel_01/02`) — centerline mostly open, coverage ≥ 0.90

UCX naming, delete/regenerate, unapplied transforms, convexity = 1.0.

### Limitations

- This is an **approximation** of V-HACD, not full voxel HACD — hulls may overlap.
- Very concave / irregular tunnels can still get local plugs; check in UE5 and raise preset / `max_hulls` if needed.
- An off-center bore (far from AABB center) may not trigger auto cavity mode — keep **Preserve Cavities** on and raise hull count, or center the opening in the asset.
- Not a replacement for hand-authored collision on critical gameplay assets.

---

## Requirements

- Blender **4.3.0+** (tested on 4.3.2+)
- Only `bpy`, `bmesh`, `mathutils` — no external libraries

---

## License

MIT

---

## Addon not showing up? (troubleshooting)

Blender only lists:

1. a **single file** `…/scripts/addons/ucx_collision_generator.py`, **or**
2. a **folder** `…/scripts/addons/ucx_collision_generator/` with `__init__.py` inside.

If you cloned the whole repo into addons as `blender-ucx-collision-generator/`, Blender **will not** see the add-on (no `__init__.py` at that folder root).

### Quick fix A (recommended)

1. Download the single file:  
   https://github.com/chr243/blender-ucx-collision-generator/raw/main/ucx_collision_generator.py
2. `Edit → Preferences → Add-ons → Install…` → pick that `.py`
3. Search **UCX** and enable the checkbox
4. Restart Blender

### Quick fix B (manual copy)

Copy **only** `ucx_collision_generator.py` (not the whole repo) to:

- Windows: `%APPDATA%\Blender Foundation\Blender\4.3\scripts\addons\`
- macOS: `~/Library/Application Support/Blender/4.3/scripts/addons/`
- Linux: `~/.config/blender/4.3/scripts/addons/`

Do not keep both a folder and a file with the same name — Blender reports *multiple addons with the same name*.  
Then Preferences → Add-ons → refresh / restart → search **UCX**.

### Other

- In Add-ons search, type `UCX` or `Collision` (the list is filtered).
- Make sure you are looking at Add-ons for Blender **4.3** (not another install).
- `Window → Toggle System Console` (Windows) — import errors show a red traceback there.

---
---

# UCX Collision Generator (PL)

Dodatek do Blendera **4.3.2+** generujący automatyczne siatki kolizji **UCX** (wypukłe / convex) pod eksport do **Unreal Engine 5**.

Przeznaczony głównie do skał, klifów i terenu — zamiast jednego AABB tworzy kilka wypukłych hulli dopasowanych do kształtu modelu.

---

## Instalacja

1. `Edycja → Preferencje → Add-ony → Zainstaluj…`
2. Wskaż **tylko** plik `ucx_collision_generator.py` (pojedynczy plik, nie cały folder repo)
3. Włącz dodatek **UCX Collision Generator**
4. W widoku 3D otwórz panel boczny (`N`) → zakładka **UCX**

Pobieranie raw:  
https://github.com/chr243/blender-ucx-collision-generator/raw/main/ucx_collision_generator.py

---

## Użycie

1. Zaznacz jeden lub więcej obiektów typu **Mesh** (np. `SM_Cliff_01`).
2. Ustaw preset (**Low / Medium / High**) lub doprecyzuj parametry.
3. Kliknij **Generate UCX Collisions**.
4. Powstaną obiekty: `UCX_SM_Cliff_01_00`, `UCX_SM_Cliff_01_01`, …
5. **Delete UCX** usuwa kolizje pasujące do zaznaczonych źródeł.
6. **Regenerate UCX** = Delete + Generate.

Źródłowa siatka **nigdy nie jest modyfikowana**. Transformacje świata UCX są kopiowane ze źródła (`matrix_world`), geometria hulli jest w lokalnej przestrzeni źródła — poprawne wyrównanie nawet przy niezaaplikowanym scale/rotation.

### Eksport FBX do UE5

- Zachowaj nazewnictwo `UCX_<StaticMeshName>_XX` (zero-padded od `_00`).
- UE5 przy imporcie FBX automatycznie rozpoznaje obiekty `UCX_*` jako convex collision.
- Eksportuj źródło razem z obiektami UCX (lub samą kolekcję `UCX_<nazwa>`).

---

## Parametry

| Parametr | Opis |
|----------|------|
| **Max Convex Hulls** | Maksymalna liczba kawałków kolizji na obiekt |
| **Target Accuracy** | Agresywność podziałów (wyżej = ciaśniejsze dopasowanie) |
| **Min Hull Size** | Odrzuć hulle o przekątnej bbox poniżej progu |
| **Geometry Simplification** | Stosunek decimate na kopii roboczej (mniej = szybciej) |
| **Max Verts Per Hull** | Limit wierzchołków na hull (`0` = bez limitu) |
| **Remove Tiny Fragments** | Próg odrzucania drobnych klastrów przed hullowaniem |
| **Collision Margin** | Ekspansja hulli od centroidu (jednostki lokalne) |
| **Create UCX Collection** | Umieść wyniki w kolekcji `UCX_<nazwa_źródła>` |
| **Preserve Cavities** | Staraj się nie zatykać tuneli/łuków (auto-wykrywanie through-bore) |

### Presety

- **Low** — 12 hulli, mocne uproszczenie (szybkie / tanie)
- **Medium** — 18 hulli, balans
- **High** — 28 hulli, drobniejszy podział

Wszystkie presety mają **Preserve Cavities** włączone domyślnie. Po wyborze presetu wartości można dalej ręcznie zmieniać.

---

## Algorytm

Blender nie ma wbudowanego V-HACD. Dodatek (v1.2+) implementuje **BFS AABB median-split na chmurze wierzchołków** + `convex_hull`:

1. Pobranie ewaluowanej siatki przez depsgraph (bez destrukcyjnego aplikowania modifierów na źródle).
2. Tymczasowy `bmesh` w **lokalnej przestrzeni** źródła + decimate.
3. Zrównoważony podział wierzchołków (BFS) aż do limitu hulli / dokładności.
4. Dla każdej części: czysty `bmesh.ops.convex_hull` (z usunięciem `geom_interior`), lekkie dopasowanie (expand), limit wierzchołków.
5. Nowe obiekty z `matrix_world = source.matrix_world`, display `WIRE`.
6. Sprzątanie tymczasowych danych.

### Preserve Cavities (v1.3)

Gdy włączone, dodatek wykrywa **przelotowy tunel/łuk** (powietrze zewnętrzne przez środek masy + otwarte końce z lokalnymi ścianami) i wtedy:

- zwiększa budżet hulli,
- dzieli klastry z dużą ilością powietrza w AABB,
- przypisuje wierzchołki po seed AABB (żeby nie scalać przeciwległych ścian tunelu),
- czyści korki w bore (test point-in-convex na próbkach centerline).

Otwarte klify / pełne skały zwykle **nie** wchodzą w ten tryb (filtr ray + stosunek odległości środka do AABB).

Przetestowane headless na Blender **4.3.2** (v1.3):

- Poly Haven `rock_07`, `boulder_01`, `namaqualand_cliff_01/02`, `rock_face_02` (CC0) — solidy
- Syntetyczne arch/tunnel (`SM_RockArch_Tunnel_01/02`) — centerline w większości otwarty, coverage ≥ 0.90

Nazewnictwo UCX, delete/regenerate, niezaaplikowany scale/rotation, convexity = 1.0.

### Ograniczenia

- To **przybliżenie** V-HACD, nie pełny Voxel HACD — hull’e mogą się nakładać.
- Bardzo wklęsłe / nieregularne tunele mogą nadal mieć lokalne „korki”; sprawdzaj w UE5 i ewentualnie podnieś preset / `max_hulls`.
- Off-center bore (tunel daleko od środka AABB) może nie włączyć auto cavity mode — wtedy zostaw **Preserve Cavities** i zwiększ liczbę hulli ręcznie, albo wycentruj otwór w assetcie.
- Nie zastępuje ręcznej kolizji w krytycznych assetach gameplayowych.

---

## Wymagania

- Blender **4.3.0+** (testowane pod 4.3.2+)
- Wyłącznie `bpy`, `bmesh`, `mathutils` — bez bibliotek zewnętrznych

---

## Licencja

MIT

---

## Nie widać addonu na liście? (troubleshooting)

Blender pokazuje na liście tylko:

1. **pojedynczy plik** `…/scripts/addons/ucx_collision_generator.py`, **albo**
2. **folder** `…/scripts/addons/ucx_collision_generator/` z plikiem `__init__.py` w środku.

Jeśli sklonowałeś całe repo do addons jako `blender-ucx-collision-generator/`, Blender **nie** zobaczy addonu (brak `__init__.py` w rootcie tego folderu).

### Szybka naprawa A (zalecana)

1. Pobierz sam plik:  
   https://github.com/chr243/blender-ucx-collision-generator/raw/main/ucx_collision_generator.py
2. `Edit → Preferences → Add-ons → Install…` → wskaż ten `.py`
3. Wyszukaj **UCX** i włącz checkbox
4. Zrestartuj Blendera

### Szybka naprawa B (ręczny copy)

Skopiuj **wyłącznie** plik `ucx_collision_generator.py` (nie całe repo) do:

- Windows: `%APPDATA%\Blender Foundation\Blender\4.3\scripts\addons\`
- macOS: `~/Library/Application Support/Blender/4.3/scripts/addons/`
- Linux: `~/.config/blender/4.3/scripts/addons/`

Nie trzymaj jednocześnie folderu i pliku o tej samej nazwie — Blender zgłosi *multiple addons with the same name*.  
Potem Preferences → Add-ons → odśwież / restart → szukaj **UCX**.

### Inne

- W polu wyszukiwania Add-ons wpisz `UCX` lub `Collision` (lista jest filtrowana).
- Upewnij się, że patrzysz na Add-ons dla wersji **4.3** (nie innej instalacji Blendera).
- `Window → Toggle System Console` (Windows) — jeśli jest błąd importu, będzie tam czerwony traceback.
