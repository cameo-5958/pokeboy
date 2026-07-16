#pragma once
#include <cstdint>
#include "state/state.h"
class Bus;
struct PPU {
    uint8_t fb[160 * 144]{};                        // shades 0-3
    uint8_t lcdc = 0x91, stat = 0x85, scy = 0, scx = 0, ly = 0, lyc = 0;
    uint8_t bgp = 0xFC, obp0 = 0, obp1 = 0, wy = 0, wx = 0;
    int     dot = 0, mode = 2, win_line = 0;
    uint8_t stat_read() const { return 0x80 | (stat & 0x78) | (lyc == ly ? 4 : 0) | mode; }
    void    stat_write(uint8_t v) { stat = v & 0x78; }
    void    tick(int tcycles, Bus& bus);
    // fb is included so a restored state shows the correct screen on the very
    // first frame rather than a stale or blank one.
    void serialize(StateIO& s) {
        s.arr(fb);
        s.v(lcdc); s.v(stat); s.v(scy); s.v(scx); s.v(ly); s.v(lyc);
        s.v(bgp); s.v(obp0); s.v(obp1); s.v(wy); s.v(wx);
        s.v(dot); s.v(mode); s.v(win_line);
    }
private:
    void render_scanline(Bus& bus);                 // Compose the current LCD scanline from VRAM and OAM.
    void set_mode(int m, Bus& bus);
};
