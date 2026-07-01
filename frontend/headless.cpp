// frontend/headless.cpp — M1 harness: runs Blargg tests, output via serial
#define _CRT_SECURE_NO_WARNINGS
#include <cstdio>
#include <vector>
#include "core.h"
int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s rom.gb\n", argv[0]); return 1; }
    FILE* f = fopen(argv[1], "rb");
    if (!f) { fprintf(stderr, "cannot open %s\n", argv[1]); return 1; }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    std::vector<uint8_t> rom(n); fread(rom.data(), 1, n, f); fclose(f);
    GameBoy gb;
    gb.load_rom(rom.data(), rom.size());
    gb.reset_post_boot();
    for (int i = 0; i < 60 * 120; i++) gb.run_frame();   // ~2 minutes of emulated time
}
