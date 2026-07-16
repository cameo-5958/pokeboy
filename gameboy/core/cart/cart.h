#pragma once
#include <cstdint>
#include <vector>
#include <memory>
struct Cartridge {
    std::vector<uint8_t> rom, ram;
    bool has_battery = false;
    static std::unique_ptr<Cartridge> create(const uint8_t* data, size_t len);
    virtual ~Cartridge() = default;
    virtual uint8_t read_rom(uint16_t a)            { return rom[a % rom.size()]; }
    // Used only for sparse mod host-call traps; normal ROM reads keep their
    // existing hot path and do not call this helper.
    virtual size_t  mapped_rom_offset(uint16_t a) const { return a % rom.size(); }
    virtual void    write_mbc(uint16_t, uint8_t)    {}
    virtual uint8_t read_ram(uint16_t)              { return 0xFF; }
    virtual void    write_ram(uint16_t, uint8_t)    {}
};
struct MBC1 : Cartridge {
    uint8_t bank_lo = 1, bank_hi = 0, mode = 0; bool ram_on = false;
    uint8_t read_rom(uint16_t a) override;
    size_t  mapped_rom_offset(uint16_t a) const override;
    void    write_mbc(uint16_t a, uint8_t v) override;
    uint8_t read_ram(uint16_t a) override;
    void    write_ram(uint16_t a, uint8_t v) override;
};
struct MBC3 : Cartridge {                            // RTC registers stubbed
    uint8_t rom_bank = 1, ram_bank = 0; bool ram_on = false;
    uint8_t read_rom(uint16_t a) override;
    size_t  mapped_rom_offset(uint16_t a) const override;
    void    write_mbc(uint16_t a, uint8_t v) override;
    uint8_t read_ram(uint16_t a) override;
    void    write_ram(uint16_t a, uint8_t v) override;
};
struct MBC5 : Cartridge {
    uint16_t rom_bank = 1; uint8_t ram_bank = 0; bool ram_on = false;
    uint8_t read_rom(uint16_t a) override;
    size_t  mapped_rom_offset(uint16_t a) const override;
    void    write_mbc(uint16_t a, uint8_t v) override;
    uint8_t read_ram(uint16_t a) override;
    void    write_ram(uint16_t a, uint8_t v) override;
};
