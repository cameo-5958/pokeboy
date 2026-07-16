#include "mod.h"

#include "cart/cart.h"
#include "cpu/cpu.h"

#include <algorithm>
#include <cctype>
#include <cstring>
#include <limits>
#include <sstream>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace gbmod {
namespace {
#include "mod_types.inc"

#include "mod_package.inc"

} // namespace

struct Runtime::Impl {
#include "mod_linker.inc"
};

Runtime::Runtime() : impl_(new Impl) {}
Runtime::~Runtime() = default;

void Runtime::on_rom_loaded(Cartridge* cartridge) {
    impl_->cartridge = cartridge;
    impl_->base_rom = cartridge ? cartridge->rom : std::vector<uint8_t>{};
    impl_->rom_symbols.clear();
    impl_->packages.clear();
    impl_->traps.clear();
    impl_->next_handle = 1;
    impl_->error.clear();
}

Status Runtime::load_symbols(const char* text, size_t len) {
    if (impl_->invoking_host)
        return impl_->fail(Status::link_error, "mods cannot be relinked from inside a host callback");
    std::unordered_map<std::string, Address> symbols;
    std::string parse_error;
    Status status = parse_symbol_document(text, len, symbols, parse_error);
    if (status != Status::ok) return impl_->fail(status, std::move(parse_error));
    if (!impl_->cartridge) return impl_->fail(Status::no_rom, "no ROM is loaded");
    LinkImage image;
    status = impl_->link(impl_->packages, symbols, image);
    if (status != Status::ok) return status;
    impl_->rom_symbols = std::move(symbols);
    impl_->commit(std::move(image));
    return Status::ok;
}

Status Runtime::load_package(const uint8_t* data, size_t len, uint32_t* handle) {
    if (!handle) return impl_->fail(Status::invalid_argument, "mod handle output pointer is null");
    *handle = 0;
    if (impl_->invoking_host)
        return impl_->fail(Status::link_error, "mods cannot be relinked from inside a host callback");
    if (!impl_->cartridge) return impl_->fail(Status::no_rom, "no ROM is loaded");
    Package package;
    std::string parse_error;
    Status status = parse_package(data, len, package, parse_error);
    if (status != Status::ok) return impl_->fail(status, std::move(parse_error));
    if (std::any_of(impl_->packages.begin(), impl_->packages.end(),
                    [&](const Package& active) { return active.id == package.id; }))
        return impl_->fail(Status::conflict, "a mod with id '" + package.id + "' is already active");
    package.handle = impl_->next_handle;
    std::vector<Package> candidates = impl_->packages;
    candidates.push_back(std::move(package));
    LinkImage image;
    status = impl_->link(candidates, impl_->rom_symbols, image);
    if (status != Status::ok) return status;
    *handle = impl_->next_handle++;
    impl_->packages = std::move(candidates);
    impl_->commit(std::move(image));
    return Status::ok;
}

Status Runtime::unload_package(uint32_t handle) {
    if (impl_->invoking_host)
        return impl_->fail(Status::link_error, "mods cannot be relinked from inside a host callback");
    const auto found = std::find_if(impl_->packages.begin(), impl_->packages.end(),
        [handle](const Package& package) { return package.handle == handle; });
    if (found == impl_->packages.end())
        return impl_->fail(Status::not_found, "mod handle is not active");
    std::vector<Package> candidates = impl_->packages;
    candidates.erase(candidates.begin() + (found - impl_->packages.begin()));
    if (candidates.empty()) {
        impl_->cartridge->rom = impl_->base_rom;
        impl_->traps.clear();
        impl_->packages.clear();
        impl_->error.clear();
        return Status::ok;
    }
    LinkImage image;
    Status status = impl_->link(candidates, impl_->rom_symbols, image);
    if (status != Status::ok) return status;
    impl_->packages = std::move(candidates);
    impl_->commit(std::move(image));
    return Status::ok;
}

size_t Runtime::package_count() const { return impl_->packages.size(); }

const char* Runtime::package_id(uint32_t handle) const {
    const Package* package = impl_->find_package(handle);
    return package ? package->id.c_str() : nullptr;
}

const char* Runtime::package_name(uint32_t handle) const {
    const Package* package = impl_->find_package(handle);
    return package ? package->name.c_str() : nullptr;
}

size_t Runtime::import_count(uint32_t handle) const {
    const Package* package = impl_->find_package(handle);
    return package ? package->imports.size() : 0;
}

const char* Runtime::import_name(uint32_t handle, size_t index) const {
    const Package* package = impl_->find_package(handle);
    return package && index < package->imports.size() ? package->imports[index].name.c_str() : nullptr;
}

const uint8_t* Runtime::metadata(uint32_t handle, size_t* len) const {
    if (len) *len = 0;
    const Package* package = impl_->find_package(handle);
    if (!package || !len || package->metadata.empty()) return nullptr;
    *len = package->metadata.size();
    return package->metadata.data();
}

void Runtime::set_host_callback(HostCallback callback, void* user) {
    impl_->callback = callback;
    impl_->callback_user = user;
}

bool Runtime::invoke_host_call(CPU& cpu, uint16_t opcode_address) {
    if (!impl_->cartridge) return false;
    const size_t offset = impl_->cartridge->mapped_rom_offset(opcode_address);
    const auto found = impl_->traps.find(offset);
    if (found == impl_->traps.end()) return false;
    const Binding binding = found->second;
    cpu.pc = static_cast<uint16_t>(cpu.pc + 2); // consume the linked 16-bit trap slot
    CpuContext context{cpu.af, cpu.bc, cpu.de, cpu.hl, cpu.sp, cpu.pc,
                       static_cast<uint8_t>(cpu.ime), static_cast<uint8_t>(cpu.halted)};
    struct HostInvocation {
        explicit HostInvocation(bool& active) : active_(active) { active_ = true; }
        ~HostInvocation() { active_ = false; }
        bool& active_;
    } invocation(impl_->invoking_host);
    if (impl_->callback)
        impl_->callback(impl_->callback_user, binding.handle, binding.import_index, context);
    cpu.af = context.af & 0xFFF0;
    cpu.bc = context.bc;
    cpu.de = context.de;
    cpu.hl = context.hl;
    cpu.sp = context.sp;
    cpu.pc = context.pc;
    cpu.ime = context.ime != 0;
    cpu.halted = context.halted != 0;
    return true;
}

const char* Runtime::last_error() const { return impl_->error.c_str(); }

} // namespace gbmod
