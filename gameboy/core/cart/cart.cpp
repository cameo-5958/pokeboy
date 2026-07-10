#include "cart.h"
std::unique_ptr<Cartridge> Cartridge::create(const uint8_t* data, size_t len) {
    if (len < 0x150) return nullptr;
    std::unique_ptr<Cartridge> c;
    uint8_t type = data[0x147];
    switch (type) {
        case 0x00:                                      c = std::make_unique<Cartridge>(); break;
        case 0x01: case 0x02: case 0x03:                c = std::make_unique<MBC1>(); break;
        case 0x0F: case 0x10: case 0x11:
        case 0x12: case 0x13:                           c = std::make_unique<MBC3>(); break;
        case 0x19: case 0x1A: case 0x1B:
        case 0x1C: case 0x1D: case 0x1E:                c = std::make_unique<MBC5>(); break;
        default:                                        c = std::make_unique<Cartridge>(); break;
    }
    switch (type) {                                     // battery-backed variants
        case 0x03: case 0x0F: case 0x10: case 0x13:
        case 0x1B: case 0x1E: c->has_battery = true; break;
    }
    c->rom.assign(data, data + len);
    static const size_t ram_sz[] = {0, 2048, 8192, 32768, 131072, 65536};
    if (data[0x149] < 6) c->ram.resize(ram_sz[data[0x149]]);
    if (c->ram.empty() && (type == 0x0F || type == 0x10))  // RTC-only carts still save
        c->ram.resize(8192);
    return c;
}

// ---- MBC1 -------------------------------------------------------------------
uint8_t MBC1::read_rom(uint16_t a) {
    uint32_t bank = (a < 0x4000) ? (mode ? (bank_hi << 5) : 0)
                                 : ((bank_hi << 5) | bank_lo);
    return rom[(bank * 0x4000 + (a & 0x3FFF)) % rom.size()];
}
size_t MBC1::mapped_rom_offset(uint16_t a) const {
    uint32_t bank = (a < 0x4000) ? (mode ? (bank_hi << 5) : 0)
                                 : ((bank_hi << 5) | bank_lo);
    return (bank * 0x4000 + (a & 0x3FFF)) % rom.size();
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

// ---- MBC3 -------------------------------------------------------------------
uint8_t MBC3::read_rom(uint16_t a) {
    uint32_t bank = (a < 0x4000) ? 0 : rom_bank;
    return rom[(bank * 0x4000 + (a & 0x3FFF)) % rom.size()];
}
size_t MBC3::mapped_rom_offset(uint16_t a) const {
    uint32_t bank = (a < 0x4000) ? 0 : rom_bank;
    return (bank * 0x4000 + (a & 0x3FFF)) % rom.size();
}
void MBC3::write_mbc(uint16_t a, uint8_t v) {
    if      (a < 0x2000) ram_on = (v & 0x0F) == 0x0A;
    else if (a < 0x4000) { rom_bank = v & 0x7F; if (!rom_bank) rom_bank = 1; }
    else if (a < 0x6000) ram_bank = v;              // 0-3 RAM, 08-0C RTC
    // 0x6000-0x7FFF: RTC latch — RTC not implemented
}
uint8_t MBC3::read_ram(uint16_t a) {
    if (!ram_on) return 0xFF;
    if (ram_bank >= 0x08) return 0;                 // RTC registers: stopped clock
    if (ram.empty()) return 0xFF;
    return ram[(ram_bank * 0x2000 + (a - 0xA000)) % ram.size()];
}
void MBC3::write_ram(uint16_t a, uint8_t v) {
    if (!ram_on || ram_bank >= 0x08 || ram.empty()) return;
    ram[(ram_bank * 0x2000 + (a - 0xA000)) % ram.size()] = v;
}

// ---- MBC5 -------------------------------------------------------------------
uint8_t MBC5::read_rom(uint16_t a) {
    uint32_t bank = (a < 0x4000) ? 0 : rom_bank;    // bank 0 is allowed here
    return rom[(bank * 0x4000 + (a & 0x3FFF)) % rom.size()];
}
size_t MBC5::mapped_rom_offset(uint16_t a) const {
    uint32_t bank = (a < 0x4000) ? 0 : rom_bank;
    return (bank * 0x4000 + (a & 0x3FFF)) % rom.size();
}
void MBC5::write_mbc(uint16_t a, uint8_t v) {
    if      (a < 0x2000) ram_on = (v & 0x0F) == 0x0A;
    else if (a < 0x3000) rom_bank = (rom_bank & 0x100) | v;
    else if (a < 0x4000) rom_bank = (rom_bank & 0xFF) | ((v & 1) << 8);
    else if (a < 0x6000) ram_bank = v & 0x0F;
}
uint8_t MBC5::read_ram(uint16_t a) {
    if (!ram_on || ram.empty()) return 0xFF;
    return ram[(ram_bank * 0x2000 + (a - 0xA000)) % ram.size()];
}
void MBC5::write_ram(uint16_t a, uint8_t v) {
    if (!ram_on || ram.empty()) return;
    ram[(ram_bank * 0x2000 + (a - 0xA000)) % ram.size()] = v;
}
