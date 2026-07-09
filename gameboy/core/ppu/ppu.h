#pragma once
#include <cstdint>
class Bus;
struct PPU {
    uint8_t fb[160 * 144]{};                        // shades 0-3
    uint8_t lcdc = 0x91, stat = 0x85, scy = 0, scx = 0, ly = 0, lyc = 0;
    uint8_t bgp = 0xFC, obp0 = 0, obp1 = 0, wy = 0, wx = 0;
    int     dot = 0, mode = 2, win_line = 0;
    uint8_t stat_read() const { return 0x80 | (stat & 0x78) | (lyc == ly ? 4 : 0) | mode; }
    void    stat_write(uint8_t v) { stat = v & 0x78; }
    void    tick(int tcycles, Bus& bus);
private:
    void render_scanline(Bus& bus);                 // full body: PDF §6.6 — paste it in at M2/M3
    void set_mode(int m, Bus& bus);
};
