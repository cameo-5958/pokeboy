#pragma once
#include <cstdint>
#include <vector>
#include <memory>
#include "state/state.h"
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
    virtual const uint8_t* battery_data(size_t* len) const;
    virtual bool load_battery_data(const uint8_t* data, size_t len);
    // `rom` is reloaded from the ROM file by the host and is never streamed;
    // `ram` is, because it is live machine state (a battery save alone would
    // miss a state taken mid-write). Each MBC adds its own bank registers:
    // restoring RAM without them would resume with the wrong bank mapped in.
    virtual void    serialize(StateIO& s)           { s.sized_bytes(ram); }
};
struct MBC1 : Cartridge {
    uint8_t bank_lo = 1, bank_hi = 0, mode = 0; bool ram_on = false;
    uint8_t read_rom(uint16_t a) override;
    size_t  mapped_rom_offset(uint16_t a) const override;
    void    write_mbc(uint16_t a, uint8_t v) override;
    uint8_t read_ram(uint16_t a) override;
    void    write_ram(uint16_t a, uint8_t v) override;
    void    serialize(StateIO& s) override {
        Cartridge::serialize(s);
        s.v(bank_lo); s.v(bank_hi); s.v(mode); s.v(ram_on);
    }
};
struct MBC3 : Cartridge {
    using ClockSource = int64_t (*)();

    explicit MBC3(bool rtc = false, ClockSource clock = nullptr);
    void set_clock_source(ClockSource clock);

    uint8_t rom_bank = 1, ram_bank = 0; bool ram_on = false;
    uint8_t read_rom(uint16_t a) override;
    size_t  mapped_rom_offset(uint16_t a) const override;
    void    write_mbc(uint16_t a, uint8_t v) override;
    uint8_t read_ram(uint16_t a) override;
    void    write_ram(uint16_t a, uint8_t v) override;
    const uint8_t* battery_data(size_t* len) const override;
    bool load_battery_data(const uint8_t* data, size_t len) override;
    void    serialize(StateIO& s) override;
private:
    int64_t now() const;
    void update_rtc() const;
    void latch_rtc();
    uint8_t read_latched_rtc() const;
    void write_rtc(uint8_t v);

    ClockSource clock_source_ = nullptr;
    mutable int64_t rtc_last_update_ = 0;
    mutable uint16_t rtc_days_ = 0;
    mutable uint8_t rtc_seconds_ = 0, rtc_minutes_ = 0, rtc_hours_ = 0;
    uint8_t latched_rtc_[5]{};
    bool has_rtc_ = false, rtc_halt_ = false;
    mutable bool rtc_carry_ = false;
    bool latch_bit_ = true;
    mutable std::vector<uint8_t> battery_buffer_;
};
struct MBC5 : Cartridge {
    uint16_t rom_bank = 1; uint8_t ram_bank = 0; bool ram_on = false;
    uint8_t read_rom(uint16_t a) override;
    size_t  mapped_rom_offset(uint16_t a) const override;
    void    write_mbc(uint16_t a, uint8_t v) override;
    uint8_t read_ram(uint16_t a) override;
    void    write_ram(uint16_t a, uint8_t v) override;
    void    serialize(StateIO& s) override {
        Cartridge::serialize(s);
        s.v(rom_bank); s.v(ram_bank); s.v(ram_on);
    }
};
