// frontend/headless.cpp - test harness: runs a ROM for N frames, prints serial
// output (Blargg tests), and can dump the final framebuffer as a BMP.
//   usage: gbemu_headless rom.gb [frames] [out.bmp]
#define _CRT_SECURE_NO_WARNINGS
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cstdlib>
#include <vector>
#include "core.h"

static void write_bmp(const char* path, const uint8_t* fb) {
    const int W = 160, H = 144;
    static const uint8_t shade[4] = { 0xFF, 0xAA, 0x55, 0x00 };
    uint8_t hdr[54] = { 'B','M' };
    uint32_t img = W * H * 3, size = 54 + img;
    memcpy(hdr + 2, &size, 4);
    hdr[10] = 54; hdr[14] = 40;
    int32_t w = W, h = H; uint16_t planes = 1, bpp = 24;
    memcpy(hdr + 18, &w, 4); memcpy(hdr + 22, &h, 4);
    memcpy(hdr + 26, &planes, 2); memcpy(hdr + 28, &bpp, 2);
    memcpy(hdr + 34, &img, 4);
    FILE* f = fopen(path, "wb");
    if (!f) return;
    fwrite(hdr, 1, 54, f);
    for (int y = H - 1; y >= 0; y--)                 // BMP is bottom-up
        for (int x = 0; x < W; x++) {
            uint8_t v = shade[fb[y * W + x] & 3];
            fputc(v, f); fputc(v, f); fputc(v, f);
        }
    fclose(f);
}

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s rom.gb [frames] [out.bmp]\n", argv[0]); return 1; }
    FILE* f = fopen(argv[1], "rb");
    if (!f) { fprintf(stderr, "cannot open %s\n", argv[1]); return 1; }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    std::vector<uint8_t> rom(n);
    size_t got = fread(rom.data(), 1, rom.size(), f); fclose(f);
    if (got != rom.size()) { fprintf(stderr, "short read on %s\n", argv[1]); return 1; }
    GameBoy gb;
    if (!gb.load_rom(rom.data(), rom.size())) { fprintf(stderr, "bad rom\n"); return 1; }
    gb.reset_post_boot();
    int frames = argc > 2 ? atoi(argv[2]) : 60 * 120;
    for (int i = 0; i < frames; i++) {
        gb.run_frame();
        gb.ai_step(pkai::monotonic_ns() + 4000000);
    }
    if (argc > 3) write_bmp(argv[3], gb.framebuffer());
    return 0;
}
