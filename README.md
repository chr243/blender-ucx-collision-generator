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

Blender nie ma wbudowanego V-HACD. Dodatek implementuje **rekurencyjną przestrzenną dekompozycję wypukłą**:

1. Pobranie ewaluowanej siatki przez depsgraph (bez aplikowania modifierów na źródle).
2. Budowa tymczasowego `bmesh` w **lokalnej przestrzeni** źródła.
3. Opcjonalny decimate kopii roboczej.
4. Rekurencyjny podział klastrów ścian wzdłuż najdłuższej osi AABB (median split środków ścian), aż do limitu hulli / wypukłości / dokładności.
5. Dla każdego klastra: `bmesh.ops.convex_hull`, margin, limit wierzchołków, filtr rozmiaru.
6. Nowe obiekty z `matrix_world = source.matrix_world`, display `WIRE`.
7. Sprzątanie tymczasowych danych.

### Ograniczenia

- To **przybliżenie** V-HACD, nie pełny algorytm Voxel HACD — wyniki mogą być mniej optymalne (więcej overlapu / mniej „szczelne” wypełnienie wklęsłości).
- Bardzo gęste / wklęsłe siatki mogą wymagać presetu **High** i ręcznego dopracowania.
- Jakość zależy od uproszczenia (`simplify`) — zbyt niski współczynnik gubi detale kształtu.
- Nie zastępuje ręcznie malowanych / autorskich kolizji w krytycznych assetach gameplayowych.

---

## Wymagania

- Blender **4.3.0+** (testowane pod 4.3.2+)
- Wyłącznie `bpy`, `bmesh`, `mathutils` — bez bibliotek zewnętrznych

---

## Licencja

MIT
