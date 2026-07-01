#include "cart.h"
std::unique_ptr<Cartridge> Cartridge::create(const uint8_t* data, size_t len) {
    if (len < 0x150) return nullptr;
    std::unique_ptr<Cartridge> c;
    switch (data[0x147]) {
        case 0x00:              c = std::make_unique<Cartridge>(); break;  // ROM only
        case 0x01: case 0x02:
        case 0x03:              c = std::make_unique<MBC1>(); break;
        // TODO(M4): 0x0F-0x13 MBC3, 0x19-0x1E MBC5 — PDF §5.3
        default:                c = std::make_unique<Cartridge>(); break;  // optimistic
    }
    c->rom.assign(data, data + len);
    static const size_t ram_sz[] = {0, 0, 8192, 32768, 131072, 65536};
    if (data[0x149] < 6) c->ram.resize(ram_sz[data[0x149]]);
    return c;
}
uint8_t MBC1::read_rom(uint16_t a) {
    uint32_t bank = (a < 0x4000) ? (mode ? (bank_hi << 5) : 0)
                                 : ((bank_hi << 5) | bank_lo);
    return rom[(bank * 0x4000 + (a & 0x3FFF)) % rom.size()];
}
void MBC1::write_mbc(uint16_t a, uint8_t v) {
    if      (a < 0x2000) ram_on = (v & 0x0F) == 0x0A;
    else if (a < 0x4000) { bank_lo = v & 0x1F; if (!bank_lo) bank_lo = 1; }
    else if (a < 0x6000) bank_hi = v & 0x03;
    else                 mode = v & 0x01;
}
uint8_t MBC1::read_ram(uint16_t a) {
    if (!ram_on || ram.empty()) return 0xFF;
    return ram[((mode ? bank_hi : 0) * 0x2000 + (a - 0xA000)) % ram.size()];
}
void MBC1::write_ram(uint16_t a, uint8_t v) {
    if (!ram_on || ram.empty()) return;
    ram[((mode ? bank_hi : 0) * 0x2000 + (a - 0xA000)) % ram.size()] = v;
}
