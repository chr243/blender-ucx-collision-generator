# UCX Collision Generator

Dodatek do Blendera **4.3.2+** generujący automatyczne siatki kolizji **UCX** (wypukłe / convex) pod eksport do **Unreal Engine 5**.

Przeznaczony głównie do skał, klifów i terenu — zamiast jednego AABB tworzy kilka wypukłych hulli dopasowanych do kształtu modelu.

---

## Instalacja (EN)

1. `Edit → Preferences → Add-ons → Install…`
2. Select `ucx_collision_generator.py`
3. Enable **UCX Collision Generator**
4. Open the **3D Viewport** sidebar (`N`) → tab **UCX**

---

## Instalacja (PL)

1. `Edycja → Preferencje → Add-ony → Zainstaluj…`
2. Wskaż plik `ucx_collision_generator.py`
3. Włącz dodatek **UCX Collision Generator**
4. W widoku 3D otwórz panel boczny (`N`) → zakładka **UCX**

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

### Presety

- **Low** — 4 hulle, mocne uproszczenie (szybkie / tanie)
- **Medium** — 10 hulli, balans
- **High** — 20 hulli, drobniejszy podział

Po wyborze presetu wartości można dalej ręcznie zmieniać.

---

## Algorytm

Blender nie ma wbudowanego V-HACD. Dodatek (v1.1+) implementuje **BFS AABB median-split na chmurze wierzchołków** + `convex_hull`:

1. Pobranie ewaluowanej siatki przez depsgraph (bez aplikowania modifierów na źródle).
2. Tymczasowy `bmesh` w **lokalnej przestrzeni** źródła + decimate.
3. Zrównoważony podział wierzchołków wzdłuż najdłuższej osi AABB, aż do limitu hulli / dokładności — każdy wierzchołek roboczy trafia do dokładnie jednej części (dobre pokrycie powierzchni).
4. Dla każdej części: czysty `bmesh.ops.convex_hull` (z usunięciem `geom_interior`), margin, limit wierzchołków, filtr rozmiaru (adaptacyjny względem rozmiaru mesha).
5. Nowe obiekty z `matrix_world = source.matrix_world`, display `WIRE`.
6. Sprzątanie tymczasowych danych.

Przetestowane headless na Blender **4.3.2** (v1.2+):
- Poly Haven `rock_07` (CC0)
- Poly Haven `namaqualand_cliff_01` (~94k tris, trudny klif)
- Poly Haven `boulder_01` (~66k tris)

Nazewnictwo UCX, delete/regenerate, niezaaplikowany scale/rotation, convexity=1.0, pokrycie wierzchołków ≈ 100% na Low/Medium/High.

### Ograniczenia

- To **przybliżenie** V-HACD, nie pełny Voxel HACD — hull’e mogą się mocniej nakładać i gorzej „wciskać” w głębokie wklęsłości.
- Bardzo wklęsłe kształty mogą wymagać presetu **High** albo ręcznego dopracowania.
- Jakość zależy od `simplify` / `max_hulls`.
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
