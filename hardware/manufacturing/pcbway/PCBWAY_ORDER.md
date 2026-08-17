# PCBWay order configuration — Pokeboy PROTO-B

**DFM HOLD. Quotation and engineering review only. Do not fabricate or assemble.**
PCBWay's confirmation of the 0.125 mm annular ring and warp behaviour is the open
gate; Octavo's paid review was superseded by a checklist self-review. Use this to
get a quote and to run the DFM discussion; do not release it to production.

Every value below is measured from `hardware/pokeboy-hw/pokeboy-hw.kicad_pcb`
unless marked as a choice. Record the generating commit SHA in the order notes.

---

## PCB Instant Quote

| Form field | Value | Why |
|---|---|---|
| Board type | Single pieces | Panelisation left to PCBWay; see notes |
| Different designs | 1 | |
| Size (X × Y) | **79.5 × 119.5 mm** | From the Gerber X2 job file |
| Quantity | 5 | Bare-board first article |
| Layers | **4** | F.Cu / In1.Cu / In2.Cu / B.Cu |
| Material | FR-4 | |
| FR4-TG | TG150–160 | Tg 150 °C minimum |
| Thickness | **0.80 mm** | |
| Min track/spacing | **4/4 mil (0.10 mm)** | Driven by *spacing*, not track — see below |
| Min hole size | **0.20 mm** | 233 of 239 holes are 0.20 mm vias |
| Solder mask | Black | |
| Silkscreen | White | |
| Edge connector | No | |
| Surface finish | **Immersion gold (ENIG)**, RoHS | |
| Via process | **Tenting vias** | Tent all except test points |
| Finished copper | 1 oz | Outer layers |
| Inner copper weight | 0.5 oz | Minimum |
| Impedance control | No | First article only; see notes |
| Castellated holes | No | The Core1106 castellations are gone |
| Gold fingers | No | |

### Why 4/4 mil and not 6/6

The spec sheet used to say "0.10 / 0.10 mm trace/space", which understated the
trace and so looked like a 4/4 mil part all round. Measured:

- **Minimum track actually routed: 0.150 mm (5.91 mil).** Only two widths exist
  on the board, 0.15 mm and 0.40 mm. Nothing is thinner than 0.15 mm.
- **Minimum spacing: 0.10 mm (3.94 mil).** Verified by tightening the clearance
  rule and re-running DRC after a zone refill: clean at 0.100 mm, 428 clearance
  violations at 0.127 mm. Real copper genuinely sits below 5 mil.

So the *spacing* forces the 4/4 mil tier. If a future revision opened spacing to
0.127 mm it would drop to 5/5 mil, because the tracks already qualify.

### Annular ring — confirm in DFM

Vias are 0.45 mm pad on 0.20 mm drill, so the annular ring is **0.125 mm
(4.92 mil)**, marginally under the 5 mil that PCBWay treats as standard. Ask
them to confirm at quote. Through-hole pads are more relaxed: 0.175 mm at U4's
thermal via and 0.30 mm at J1's board-lock posts.

---

## Assembly (PCBA)

| Form field | Value | Why |
|---|---|---|
| Assembly side | **Top side only** | All 110 footprints are top-side |
| Assembly quantity | 2 | Assembly first article |
| Unique parts | **50** | Placeable BOM lines; 57 total less 3 DNP and 4 off-board |
| Total placements | **103** | CPL rows; 113 footprints less 3 DNP and 7 pad-only |
| SMD placements | 103 | Every placed part is SMD |
| BGA / QFP parts | **1 BGA** — U1, 256 balls, 1.27 mm pitch | Plus 5 QFN/WSON, see below |
| Through-hole parts | **0** | J1 has 4 through-hole board-lock posts but is an SMD connector |
| Stencil | **Yes, top only** | `B_Paste` is intentionally near-empty |
| Parts sourcing | Your choice — turnkey or consigned | |

### Fine-pitch parts to call out

| Ref | Part | Package | Lead pitch |
|---|---|---|---|
| U1 | OSD3358-1G-ISM | BGA-256, 21 × 21 mm | 1.27 mm |
| J5 | EA TFT020-23AINN FFC | 39-way | 0.30 mm |
| U5 | TPS61165DRV | WSON-6 | 0.40 mm |
| U6 | MAX98357AETE+T | TQFN-16 | 0.44 mm |
| U2 | BQ24074RGT | VQFN-16 | 0.50 mm |
| U3 | TPS63802DLAR | DLA-10 | 0.50 mm |
| U4 | TPS62162DSG | WSON-8 | 0.50 mm |

U4's thermal pad sits on a 0.10 mm via array. That is a pad-to-pad spacing, not
a lead pitch — do not read it as a 0.10 mm placement requirement.

### Do not fit

**R4**, **R9** and **R19** are DNP. They are in the BOM marked do-not-fit and are
already excluded from `CPL.csv`, so the CPL is the authority: 103 rows, not 113.

---

## Notes for the order

1. **DFM HOLD** — quotation and engineering review only.
2. **100% X-ray on U1.** Direct-mount BGA-256 on a 0.80 mm board. Images
   required with the first article; judge voiding and bridging to IPC-A-610
   Class 2.
3. **Panelisation** — do not place breakaway tabs at the control cluster or the
   microSD opening. PCBWay standard tab/route otherwise.
4. **Board is thin at 0.80 mm with a 21 mm BGA.** Ask them to confirm warp and
   the reflow profile at quote.
5. **Electrical test** — flying probe against the supplied `fab/pokeboy-hw.d356`
   IPC-D-356 netlist.
6. **Impedance is not controlled** for this article, but keep the USB D+/D−
   pair's routed geometry intact; do not let CAM reroute or re-space it.
7. **Acceptability** — IPC-A-600 Class 2 bare board, IPC-A-610 Class 2 assembly.
8. All heights are top-side; the tallest parts are the 5.00 mm face buttons.
   Nothing on the bottom, so bottom-side handling is unconstrained.

## Files to upload

| Purpose | File |
|---|---|
| PCB fabrication | `pokeboy-hw-PROTO-B-gerbers.zip` (Gerber X2 + Excellon drill + job file) |
| Bare-board test | `fab/pokeboy-hw.d356` (inside the zip) |
| Assembly BOM | `BOM.csv` |
| Assembly placement | `CPL.csv` |
| Layer key | `fab/LAYERS.txt` (inside the zip) |
