#include "cart.h"
#include <algorithm>
#include <ctime>

namespace {
int64_t system_clock_seconds() {
    return static_cast<int64_t>(std::time(nullptr));
}
}

const uint8_t* Cartridge::battery_data(size_t* len) const {
    *len = ram.size();
    return ram.empty() ? nullptr : ram.data();
}

bool Cartridge::load_battery_data(const uint8_t* data, size_t len) {
    if (!data || len > ram.size()) return false;
    std::copy(data, data + len, ram.begin());
    return true;
}

std::unique_ptr<Cartridge> Cartridge::create(const uint8_t* data, size_t len) {
    if (len < 0x150) return nullptr;
    std::unique_ptr<Cartridge> c;
    uint8_t type = data[0x147];
    switch (type) {
        case 0x00:                                      c = std::make_unique<Cartridge>(); break;
        case 0x01: case 0x02: case 0x03:                c = std::make_unique<MBC1>(); break;
        case 0x0F: case 0x10:                           c = std::make_unique<MBC3>(true); break;
        case 0x11: case 0x12: case 0x13:                c = std::make_unique<MBC3>(); break;
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
MBC3::MBC3(bool rtc, ClockSource clock)
    : clock_source_(clock ? clock : system_clock_seconds), has_rtc_(rtc) {
    rtc_last_update_ = now();
}

void MBC3::set_clock_source(ClockSource clock) {
    clock_source_ = clock ? clock : system_clock_seconds;
    rtc_last_update_ = now();
}

int64_t MBC3::now() const {
    return clock_source_();
}

void MBC3::update_rtc() const {
    const int64_t current = now();
    if (current <= rtc_last_update_) return;
    const uint64_t elapsed = static_cast<uint64_t>(current - rtc_last_update_);
    rtc_last_update_ = current;
    if (rtc_halt_) return;

    constexpr uint64_t seconds_per_day = 24 * 60 * 60;
    constexpr uint64_t rtc_period = 512 * seconds_per_day;
    uint64_t total = rtc_seconds_ + 60ULL * rtc_minutes_ +
        3600ULL * rtc_hours_ + seconds_per_day * rtc_days_ + elapsed;
    if (total >= rtc_period) rtc_carry_ = true;
    total %= rtc_period;
    rtc_days_ = static_cast<uint16_t>(total / seconds_per_day);
    total %= seconds_per_day;
    rtc_hours_ = static_cast<uint8_t>(total / 3600);
    total %= 3600;
    rtc_minutes_ = static_cast<uint8_t>(total / 60);
    rtc_seconds_ = static_cast<uint8_t>(total % 60);
}

void MBC3::latch_rtc() {
    update_rtc();
    latched_rtc_[0] = rtc_seconds_;
    latched_rtc_[1] = rtc_minutes_;
    latched_rtc_[2] = rtc_hours_;
    latched_rtc_[3] = static_cast<uint8_t>(rtc_days_);
    latched_rtc_[4] = static_cast<uint8_t>((rtc_days_ >> 8) |
        (rtc_halt_ ? 0x40 : 0) | (rtc_carry_ ? 0x80 : 0));
}

uint8_t MBC3::read_latched_rtc() const {
    return ram_bank >= 0x08 && ram_bank <= 0x0C
        ? latched_rtc_[ram_bank - 0x08]
        : 0xFF;
}

void MBC3::write_rtc(uint8_t v) {
    update_rtc();
    switch (ram_bank) {
        case 0x08: rtc_seconds_ = v & 0x3F; break;
        case 0x09: rtc_minutes_ = v & 0x3F; break;
        case 0x0A: rtc_hours_ = v & 0x1F; break;
        case 0x0B: rtc_days_ = (rtc_days_ & 0x100) | v; break;
        case 0x0C:
            rtc_days_ = (rtc_days_ & 0xFF) | ((v & 0x01) << 8);
            rtc_halt_ = (v & 0x40) != 0;
            rtc_carry_ = (v & 0x80) != 0;
            break;
    }
}

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
    else if (a < 0x6000) ram_bank = v;
    else if (has_rtc_) {
        const bool next_latch_bit = (v & 0x01) != 0;
        if (!latch_bit_ && next_latch_bit) latch_rtc();
        latch_bit_ = next_latch_bit;
    }
}
uint8_t MBC3::read_ram(uint16_t a) {
    if (!ram_on) return 0xFF;
    if (ram_bank >= 0x08) return has_rtc_ ? read_latched_rtc() : 0xFF;
    if (ram.empty()) return 0xFF;
    return ram[(ram_bank * 0x2000 + (a - 0xA000)) % ram.size()];
}
void MBC3::write_ram(uint16_t a, uint8_t v) {
    if (!ram_on) return;
    if (ram_bank >= 0x08) {
        if (has_rtc_ && ram_bank <= 0x0C) write_rtc(v);
        return;
    }
    if (ram.empty()) return;
    ram[(ram_bank * 0x2000 + (a - 0xA000)) % ram.size()] = v;
}

const uint8_t* MBC3::battery_data(size_t* len) const {
    if (!has_rtc_) return Cartridge::battery_data(len);
    update_rtc();

    constexpr size_t trailer_size = 25;
    battery_buffer_ = ram;
    battery_buffer_.reserve(ram.size() + trailer_size);
    const uint8_t header[] = { 'M', '3', 'R', 'T', 1,
        rtc_seconds_, rtc_minutes_, rtc_hours_,
        static_cast<uint8_t>(rtc_days_), static_cast<uint8_t>(rtc_days_ >> 8),
        static_cast<uint8_t>((rtc_halt_ ? 0x40 : 0) | (rtc_carry_ ? 0x80 : 0)) };
    battery_buffer_.insert(battery_buffer_.end(), header, header + sizeof(header));
    battery_buffer_.insert(battery_buffer_.end(), latched_rtc_, latched_rtc_ + 5);
    battery_buffer_.push_back(latch_bit_ ? 1 : 0);
    const uint64_t timestamp = static_cast<uint64_t>(rtc_last_update_);
    for (int shift = 0; shift < 64; shift += 8)
        battery_buffer_.push_back(static_cast<uint8_t>(timestamp >> shift));
    *len = battery_buffer_.size();
    return battery_buffer_.data();
}

bool MBC3::load_battery_data(const uint8_t* data, size_t len) {
    if (!data) return false;
    if (!has_rtc_ || len <= ram.size()) {
        if (!Cartridge::load_battery_data(data, len)) return false;
        if (has_rtc_) {
            rtc_last_update_ = now();
            rtc_days_ = 0;
            rtc_seconds_ = rtc_minutes_ = rtc_hours_ = 0;
            std::fill(latched_rtc_, latched_rtc_ + 5, 0);
            rtc_halt_ = rtc_carry_ = false;
            latch_bit_ = true;
        }
        return true;
    }

    constexpr size_t trailer_size = 25;
    if (len != ram.size() + trailer_size) return false;
    const uint8_t* trailer = data + ram.size();
    if (trailer[0] != 'M' || trailer[1] != '3' || trailer[2] != 'R' ||
        trailer[3] != 'T' || trailer[4] != 1 || trailer[9] > 1 ||
        trailer[16] > 1) return false;

    uint64_t timestamp = 0;
    for (int shift = 0; shift < 64; shift += 8)
        timestamp |= static_cast<uint64_t>(trailer[17 + shift / 8]) << shift;

    std::copy(data, data + ram.size(), ram.begin());
    rtc_seconds_ = trailer[5];
    rtc_minutes_ = trailer[6];
    rtc_hours_ = trailer[7];
    rtc_days_ = static_cast<uint16_t>(trailer[8] | (trailer[9] << 8));
    rtc_halt_ = (trailer[10] & 0x40) != 0;
    rtc_carry_ = (trailer[10] & 0x80) != 0;
    std::copy(trailer + 11, trailer + 16, latched_rtc_);
    latch_bit_ = trailer[16] != 0;
    rtc_last_update_ = static_cast<int64_t>(timestamp);
    return true;
}

void MBC3::serialize(StateIO& s) {
    if (s.saving() && has_rtc_) update_rtc();
    Cartridge::serialize(s);
    s.v(rom_bank); s.v(ram_bank); s.v(ram_on);
    s.v(rtc_last_update_); s.v(rtc_days_);
    s.v(rtc_seconds_); s.v(rtc_minutes_); s.v(rtc_hours_);
    s.arr(latched_rtc_);
    s.v(rtc_halt_); s.v(rtc_carry_); s.v(latch_bit_);
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
