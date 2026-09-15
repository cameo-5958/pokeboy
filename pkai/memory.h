#pragma once
#include <cstddef>
#include <cstdint>
#include "generated/wram_symbols.h"
#include "generated/rom_tables.h"
namespace pkai {
struct Memory {
    void* context{};
    uint8_t (*read_byte)(void*, uint16_t){};
    void (*write_byte)(void*, uint16_t, uint8_t){};
    const uint8_t* rom{};
    size_t rom_size{};
    uint8_t read(uint16_t a) const { return read_byte(context,a); }
    uint16_t word(uint16_t a) const { return uint16_t(read(a) << 8) | read(a+1); }
    void write(uint16_t a, uint8_t v) const { write_byte(context,a,v); }
    uint8_t table(tables::Address a, unsigned i) const {
        return a.offset()+i < rom_size ? rom[a.offset()+i] : 0;
    }
};
}
