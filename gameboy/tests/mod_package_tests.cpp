#include "gb_api.h"

#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace {

std::vector<uint8_t> read_bytes(const char* path) {
    std::ifstream file(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}

std::string read_text(const char* path) {
    std::ifstream file(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}

bool expect_bytes(gb_handle* gb, uint8_t bank, uint16_t address,
                  const std::vector<uint8_t>& expected) {
    gb_write_mem(gb, 0x2000, bank);
    for (size_t i = 0; i < expected.size(); ++i) {
        const uint8_t actual = gb_read_mem(gb, static_cast<uint16_t>(address + i));
        if (actual != expected[i]) {
            std::fprintf(stderr, "bank %02x:%04x: expected %02x, got %02x\n",
                         bank, static_cast<unsigned>(address + i), expected[i], actual);
            return false;
        }
    }
    return true;
}

} // namespace

int main(int argc, char** argv) {
    if (argc != 4) {
        std::fprintf(stderr, "usage: %s rom.gb symbols.sym package.gbmod\n", argv[0]);
        return 2;
    }
    const auto rom = read_bytes(argv[1]);
    const auto symbols = read_text(argv[2]);
    const auto package = read_bytes(argv[3]);
    if (rom.empty() || symbols.empty() || package.empty()) {
        std::fputs("failed to read a test input\n", stderr);
        return 2;
    }

    gb_handle* gb = gb_create();
    if (!gb || !gb_load_rom(gb, rom.data(), rom.size())) return 1;
    gb_write_mem(gb, 0xff50, 1); // inspect cartridge ROM, not the custom boot overlay
    if (gb_mod_load_symbols(gb, symbols.data(), symbols.size()) != GB_MOD_OK) {
        std::fprintf(stderr, "symbols: %s\n", gb_mod_last_error(gb));
        return 1;
    }
    uint32_t handle = 0;
    if (gb_mod_load(gb, package.data(), package.size(), &handle) != GB_MOD_OK) {
        std::fprintf(stderr, "package: %s\n", gb_mod_last_error(gb));
        return 1;
    }

    bool ok = gb_mod_import_count(gb, handle) == 0;
    ok &= expect_bytes(gb, 0x01, 0x71c5,
                       {0x3e, 0x12, 0x21, 0x8f, 0x63, 0xcd, 0xd6, 0x35, 0xc9});
    ok &= expect_bytes(gb, 0x12, 0x638f, {0xfa, 0x63, 0xd1, 0xa7, 0xc8});
    ok &= expect_bytes(gb, 0x1c, 0x5c0f, {0xcd, 0x9d, 0x7b});
    ok &= expect_bytes(gb, 0x1c, 0x5c67, {0xcd, 0xa9, 0x7b});
    ok &= expect_bytes(gb, 0x1c, 0x7b9d, {0xaf, 0xea, 0x92, 0xcf});
    ok &= gb_mod_unload(gb, handle) == GB_MOD_OK;
    ok &= expect_bytes(gb, 0x01, 0x71c5,
                       {0x21, 0xb8, 0x72, 0xcd, 0x49, 0x3c, 0xfa, 0x4b, 0xd7});
    ok &= expect_bytes(gb, 0x1c, 0x5c0f, {0xcd, 0xfc, 0x13});
    gb_destroy(gb);

    if (!ok) return 1;
    std::puts("compiled mod package link/unload test passed");
    return 0;
}
