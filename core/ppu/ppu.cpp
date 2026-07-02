#include "ppu.h"
#include "bus/bus.h"
#include <cstring>

void PPU::set_mode(int m, Bus& bus) {
    mode = m;
    static const uint8_t int_bit[4] = {0x08, 0x10, 0x20, 0x00};  // STAT enables m0,m1,m2
    if (int_bit[m] & stat) bus.if_reg |= 0x02;
}

void PPU::tick(int tcycles, Bus& bus) {
    if (!(lcdc & 0x80)) { ly = 0; dot = 0; mode = 0; return; }   // LCD off
    dot += tcycles;
    while (dot >= 456) {
        dot -= 456;
        if (ly < 144 && mode != 1) render_scanline(bus);         // draw at end of line
        ly++;
        if (ly == 144) { set_mode(1, bus); bus.if_reg |= 0x01; } // VBlank interrupt
        if (ly > 153)  { ly = 0; win_line = 0; }
        if (ly < 144)  set_mode(2, bus);
        if (ly == lyc && (stat & 0x40)) bus.if_reg |= 0x02;      // LYC coincidence
    }
    if (ly < 144) {                                              // mode progression in-line
        if      (dot < 80  && mode != 2) set_mode(2, bus);
        else if (dot >= 80 && dot < 252 && mode != 3) mode = 3;  // mode 3: no STAT int
        else if (dot >= 252 && mode != 0) set_mode(0, bus);
    }
}

void PPU::render_scanline(Bus& bus) {
    uint8_t* line = fb + ly * 160;
    uint8_t bg_ci[160];                              // raw color index, for OBJ priority
    memset(bg_ci, 0, sizeof(bg_ci));
    memset(line, 0, 160);

    const bool tiles_8000 = lcdc & 0x10;
    auto tile_row = [&](uint8_t tile, int row, uint8_t& lo, uint8_t& hi) {
        uint16_t base = tiles_8000 ? tile * 16 : (uint16_t)(0x1000 + (int8_t)tile * 16);
        lo = bus.vram[base + row * 2];
        hi = bus.vram[base + row * 2 + 1];
    };

    if (lcdc & 0x01) {                               // background
        uint16_t map = (lcdc & 0x08) ? 0x1C00 : 0x1800;
        uint8_t y = (uint8_t)(scy + ly);
        for (int x = 0; x < 160; x++) {
            uint8_t px = (uint8_t)(scx + x);
            uint8_t tile = bus.vram[map + (y / 8) * 32 + (px / 8)];
            uint8_t lo, hi;
            tile_row(tile, y & 7, lo, hi);
            int bit = 7 - (px & 7);
            uint8_t ci = (uint8_t)((((hi >> bit) & 1) << 1) | ((lo >> bit) & 1));
            bg_ci[x] = ci;
            line[x] = (bgp >> (ci * 2)) & 3;
        }
    }

    if ((lcdc & 0x20) && (lcdc & 0x01) && ly >= wy && wx <= 166) {   // window
        uint16_t map = (lcdc & 0x40) ? 0x1C00 : 0x1800;
        bool drew = false;
        for (int x = 0; x < 160; x++) {
            int wxp = x - ((int)wx - 7);
            if (wxp < 0) continue;
            drew = true;
            uint8_t tile = bus.vram[map + (win_line / 8) * 32 + (wxp / 8)];
            uint8_t lo, hi;
            tile_row(tile, win_line & 7, lo, hi);
            int bit = 7 - (wxp & 7);
            uint8_t ci = (uint8_t)((((hi >> bit) & 1) << 1) | ((lo >> bit) & 1));
            bg_ci[x] = ci;
            line[x] = (bgp >> (ci * 2)) & 3;
        }
        if (drew) win_line++;
    }

    if (lcdc & 0x02) {                               // sprites
        int hgt = (lcdc & 0x04) ? 16 : 8;
        int sel[10], count = 0;
        for (int i = 0; i < 40 && count < 10; i++) {  // OAM scan: first 10 on this line
            int sy = bus.oam[i * 4] - 16;
            if (ly >= sy && ly < sy + hgt) sel[count++] = i;
        }
        // Lower X wins overlaps (tie: lower OAM index). Draw lowest-priority first
        // so higher-priority sprites overwrite: sort descending by (x, index).
        for (int i = 1; i < count; i++) {
            int k = sel[i];
            int kx = bus.oam[k * 4 + 1];
            int j = i - 1;
            while (j >= 0 && (bus.oam[sel[j] * 4 + 1] < kx ||
                              (bus.oam[sel[j] * 4 + 1] == kx && sel[j] < k))) {
                sel[j + 1] = sel[j]; j--;
            }
            sel[j + 1] = k;
        }
        for (int s = 0; s < count; s++) {
            const uint8_t* e = &bus.oam[sel[s] * 4];
            int sy = e[0] - 16, sx = e[1] - 8;
            uint8_t tile = e[2], attr = e[3];
            int row = ly - sy;
            if (attr & 0x40) row = hgt - 1 - row;    // Y flip
            if (hgt == 16) { tile = (tile & 0xFE) | (row >> 3); row &= 7; }
            uint8_t lo = bus.vram[tile * 16 + row * 2];
            uint8_t hi = bus.vram[tile * 16 + row * 2 + 1];
            uint8_t pal = (attr & 0x10) ? obp1 : obp0;
            for (int px = 0; px < 8; px++) {
                int x = sx + px;
                if (x < 0 || x >= 160) continue;
                int bit = (attr & 0x20) ? px : 7 - px;   // X flip
                uint8_t ci = (uint8_t)((((hi >> bit) & 1) << 1) | ((lo >> bit) & 1));
                if (ci == 0) continue;                   // transparent
                if ((attr & 0x80) && bg_ci[x] != 0) continue;   // behind BG colors 1-3
                line[x] = (pal >> (ci * 2)) & 3;
            }
        }
    }
}
