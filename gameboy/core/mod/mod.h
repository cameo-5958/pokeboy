#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>

struct CPU;
struct Cartridge;

namespace gbmod {

// Keep these values in sync with gb_mod_status in adapter/gb_api.h.
enum class Status : int {
    ok = 0,
    invalid_argument = 1,
    no_rom = 2,
    bad_symbols = 3,
    bad_package = 4,
    unsupported_version = 5,
    rom_mismatch = 6,
    missing_symbol = 7,
    conflict = 8,
    link_error = 9,
    not_found = 10,
};

struct CpuContext {
    uint16_t af, bc, de, hl, sp, pc;
    uint8_t ime, halted;
};

using HostCallback = void (*)(void* user, uint32_t mod_handle,
                              uint32_t import_index, CpuContext& context);

// Owns the pristine ROM image, active packages, link state, and the sparse
// host-call trap table. Linking happens only when packages/symbols change.
class Runtime {
public:
    Runtime();
    ~Runtime();
    Runtime(const Runtime&) = delete;
    Runtime& operator=(const Runtime&) = delete;

    void on_rom_loaded(Cartridge* cartridge);
    Status load_symbols(const char* text, size_t len);
    Status load_package(const uint8_t* data, size_t len, uint32_t* handle);
    Status unload_package(uint32_t handle);

    size_t package_count() const;
    const char* package_id(uint32_t handle) const;
    const char* package_name(uint32_t handle) const;
    size_t import_count(uint32_t handle) const;
    const char* import_name(uint32_t handle, size_t index) const;
    const uint8_t* metadata(uint32_t handle, size_t* len) const;

    void set_host_callback(HostCallback callback, void* user);
    bool invoke_host_call(CPU& cpu, uint16_t opcode_address);

    const char* last_error() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace gbmod
