# Pokeboy PROTO-B — PCBWay handoff

Status: **DFM HOLD — quotation and engineering review only. Do not fabricate or
assemble.**

This package describes the OSD3358-1G-ISM / 1 GB DDR3L revision. It is suitable
for a PCBWay process/assembly quotation and DFM discussion. Connectivity is now
complete: KiCad DRC reports zero errors, zero unconnected items and no
short-circuit or spacing violations, ERC reports zero errors, and the schematic
netlist and the board connectivity are identical net for net.

Fabrication data is in `fab/` and zipped as `pokeboy-hw-PROTO-B-gerbers.zip`:
Gerber X2 for the four copper layers plus mask, legend, paste and profile, an
X2 job file declaring the stack, separated plated/non-plated Excellon drill with
maps, and an IPC-D-356 netlist for bare-board test. `fab/LAYERS.txt` maps every
filename to its layer. Publishing it does not lift the hold — it exists so the
quotation and the DFM discussion can run against real data. Do not fabricate.

Remaining warnings are 36 non-blocking items: silkscreen overlap/clipping,
reference-text size on the U1 and Y1 library footprints, and the Y1 library
footprint mismatch. The former dangling track stubs and isolated pour fragments
have been removed from the board; all remaining warnings are cosmetic
silkscreen/text items. None affect connectivity.

U1's ball map has been checked ball by ball against Octavo's own PocketBeagle
reference design (OSD3358-512M-BSM, identical BGA-256 ball map), and every
peripheral assignment agrees with the documented signal names. That check also
found four balls the reference design ties that this board had left floating —
`PMIC_POWER_EN`/`PMIC_PWR_EN`, `PMIC_LDO_PGOOD`/`RTC_PWRONRSTN`,
`PMIC_PGOOD`/`PWRONRSTN` and the ADC reference pair — which are now connected.
The first of those gates the PMIC's enable, so the previous revision would not
have powered up.

Release gate

1. ~~Close all connectivity items in `DFM_DRC_REPORT.txt`.~~ Done — 0 unconnected.
2. ~~Run KiCad ERC and DRC.~~ Done — ERC 0 errors, DRC 0 errors / 0 unconnected.
3. ~~Obtain Octavo's OSD335x design review.~~ Superseded — Octavo's published
   OSD335x schematic checklist was executed item by item as a self-review, and
   every mandatory finding is fixed on the board (SYS_VOUT bulk capacitance,
   ground stitching under U1, VIN_BAT↔PMIC_BAT_SENSE and VIN_USB power-mode
   ties, EEPROM_WP). The paid review is intentionally not purchased; the
   5-board first article is the physical review, with risk bounded by a respin.
   **The open gate is PCBWay's confirmation of the 0.125 mm annular ring and
   warp behavior on the 0.80 mm stack with the 21 mm BGA** (asked 2026-08-16,
   awaiting reply).
4. Regenerate BOM, CPL, Gerbers, drill files, IPC-356 netlist, and assembly PDFs
   from the same Git commit; record its SHA in the order notes.
5. Approve a five-board bare-PCB first article and two-board assembly first
   article only after PCBWay confirms the stack-up and BGA process.

PCB specification

- Finished size: board outline in KiCad source (nominal 79.5 × 119.5 mm).
- Layers: 4.
- Finished thickness: 0.80 mm.
- Copper: 1 oz outer / 0.5 oz inner minimum.
- Surface finish: ENIG, RoHS.
- Solder mask / legend: black mask, white legend.
- Minimum signal geometry: 0.15 mm trace / 0.10 mm space.
- Through vias: 0.45 mm pad / 0.20 mm finished drill; tent non-test vias.
- Controlled impedance: not required for this first article; keep USB D+/D− as
  a coupled 90-ohm differential pair where routed.
- Acceptability: IPC-A-600 Class 2 bare board; IPC-A-610 Class 2 assembly.

Assembly notes

- U1 `OSD3358-1G-ISM` is MSL-sensitive and should be treated as customer-
  consigned unless PCBWay confirms traceable authorized distribution stock.
- Use a laser-cut stepped stencil if PCBWay recommends one after paste review.
- X-ray U1 on every assembled first-article board; provide images with the order.
- J5 display, battery, speaker, and removable microSD card are off-board/final-
  assembly items and are not fitted by SMT assembly.
- DNP R4, R9 and R19. Do not substitute the three tactile-switch families without
  written approval; force and travel are intentional.
- Program/boot media: validated microSD image is required before functional test.
- Electrical test points: USB VBUS/D+/D−, SYS_5V, AUX_3V3, OSD rails, reset,
  UART0, LCD clock, and every control input.

Files

- `BOM.csv`: grouped exact manufacturer part numbers and per-board quantities.
- `CPL.csv`: KiCad centroid export in millimetres; PCBWay must apply its KiCad
  rotation convention during DFM review.
- `PCB_SPEC.csv`: order-form values and release gates.
- `DFM_DRC_REPORT.txt`: authoritative current hold list.

Reference design: BeagleBoard.org PocketBeagle. The OSD3358 footprint attribution
and CC BY 4.0 notice are in `hardware/pokeboy-hw/THIRD_PARTY_NOTICES.md`.
