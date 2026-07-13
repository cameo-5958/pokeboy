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

bool byte_is(gb_handle* gb, uint8_t bank, uint16_t address, uint8_t expected) {
    gb_write_mem(gb, 0x2000, bank);
    const uint8_t actual = gb_read_mem(gb, address);
    if (actual == expected) return true;
    std::fprintf(stderr, "%02x:%04x expected %02x, got %02x\n",
                 bank, address, expected, actual);
    return false;
}

bool call_targets_fixed_section(gb_handle* gb, uint16_t address) {
    gb_write_mem(gb, 0x2000, 0x0f);
    if (gb_read_mem(gb, address) != 0xcd) return false;
    const uint16_t target = static_cast<uint16_t>(gb_read_mem(gb, address + 1) |
                                                  (gb_read_mem(gb, address + 2) << 8));
    return target >= 0x7e00 && target < 0x7f00;
}

} // namespace

int main(int argc, char** argv) {
    if (argc != 4) {
        std::fprintf(stderr, "usage: %s rom.gb symbols.sym battle-link.gbmod\n", argv[0]);
        return 2;
    }
    const auto rom = read_bytes(argv[1]);
    const auto symbols = read_text(argv[2]);
    const auto package = read_bytes(argv[3]);
    gb_handle* gb = gb_create();
    if (!gb || !gb_load_rom(gb, rom.data(), rom.size())) return 1;
    gb_write_mem(gb, 0xff50, 1);
    if (gb_mod_load_symbols(gb, symbols.data(), symbols.size()) != GB_MOD_OK) return 1;
    uint32_t handle = 0;
    if (gb_mod_load(gb, package.data(), package.size(), &handle) != GB_MOD_OK) {
        std::fprintf(stderr, "%s\n", gb_mod_last_error(gb));
        return 1;
    }

    bool ok = gb_mod_import_count(gb, handle) == 1;
    ok &= std::string(gb_mod_import_name(gb, handle, 0)) == "battle-link.decide";
    ok &= call_targets_fixed_section(gb, 0x411e);
    ok &= call_targets_fixed_section(gb, 0x42a6);
    ok &= call_targets_fixed_section(gb, 0x4341);
    ok &= call_targets_fixed_section(gb, 0x4397);
    ok &= call_targets_fixed_section(gb, 0x4969);
    ok &= byte_is(gb, 0x0f, 0x7e00, 0x3e); // ld a, 4; host reset trap follows
    ok &= gb_mod_unload(gb, handle) == GB_MOD_OK;
    ok &= byte_is(gb, 0x0f, 0x411e, 0xaf);
    ok &= byte_is(gb, 0x0f, 0x42a6, 0xcd);
    ok &= byte_is(gb, 0x0f, 0x4341, 0x21);
    ok &= byte_is(gb, 0x0f, 0x4397, 0x21);
    ok &= byte_is(gb, 0x0f, 0x4969, 0x06);
    gb_destroy(gb);
    if (!ok) return 1;
    std::puts("Battle Link guarded package link/unload test passed");
    return 0;
}
