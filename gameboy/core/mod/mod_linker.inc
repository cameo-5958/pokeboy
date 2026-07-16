    Cartridge* cartridge = nullptr;
    std::vector<uint8_t> base_rom;
    std::unordered_map<std::string, Address> rom_symbols;
    std::vector<Package> packages;
    std::unordered_map<size_t, Binding> traps;
    uint32_t next_handle = 1;
    HostCallback callback = nullptr;
    void* callback_user = nullptr;
    bool invoking_host = false;
    std::string error;

    Status fail(Status status, std::string message) {
        error = std::move(message);
        return status;
    }

    const Package* find_package(uint32_t handle) const {
        const auto found = std::find_if(packages.begin(), packages.end(),
            [handle](const Package& package) { return package.handle == handle; });
        return found == packages.end() ? nullptr : &*found;
    }

    Status resolve_named(const std::string& name,
                         const std::unordered_map<std::string, Address>& symbols,
                         Address& address) {
        const auto found = symbols.find(name);
        if (found == symbols.end())
            return fail(Status::missing_symbol, "ROM symbol '" + name + "' was not provided");
        address = found->second;
        if (address.offset >= base_rom.size())
            return fail(Status::missing_symbol, "ROM symbol '" + name + "' lies outside the ROM");
        return Status::ok;
    }

    Status reserve_range(std::vector<int32_t>& owners, size_t offset, size_t size,
                         int32_t owner, const Package& package, const char* kind) {
        if (offset > owners.size() || size > owners.size() - offset)
            return fail(Status::link_error, std::string(kind) + " in mod '" + package.id +
                        "' lies outside the linked ROM");
        for (size_t i = offset; i < offset + size; ++i)
            if (owners[i] != -1)
                return fail(Status::conflict, std::string(kind) + " in mod '" + package.id +
                            "' overlaps another active mod");
        std::fill(owners.begin() + offset, owners.begin() + offset + size, owner);
        return Status::ok;
    }

    Status link(const std::vector<Package>& candidates,
                const std::unordered_map<std::string, Address>& symbols,
                LinkImage& image) {
        if (!cartridge || base_rom.empty()) return fail(Status::no_rom, "no ROM is loaded");
        if (candidates.empty()) {
            image.rom = base_rom;
            image.traps.clear();
            return Status::ok;
        }
        const uint32_t base_crc = crc32(base_rom.data(), base_rom.size());
        for (const Package& package : candidates) {
            if (package.target_size && package.target_size != base_rom.size())
                return fail(Status::rom_mismatch, "mod '" + package.id + "' targets a different ROM size");
            if (package.target_crc32 && package.target_crc32 != base_crc)
                return fail(Status::rom_mismatch, "mod '" + package.id + "' targets a different ROM CRC32");
        }

        image.rom = base_rom;
        image.traps.clear();
        std::vector<int32_t> owners(image.rom.size(), -1);
        std::vector<std::vector<Address>> section_addresses(candidates.size());
        std::vector<std::vector<Address>> patch_addresses(candidates.size());
        std::vector<std::vector<Address>> export_addresses(candidates.size());
        std::vector<std::vector<uint16_t>> import_slots(candidates.size());
        std::vector<Binding> bindings;

        for (size_t p = 0; p < candidates.size(); ++p) {
            import_slots[p].reserve(candidates[p].imports.size());
            for (size_t i = 0; i < candidates[p].imports.size(); ++i) {
                if (bindings.size() >= 0x10000)
                    return fail(Status::link_error, "active mods exceed the 65536 host-import limit");
                import_slots[p].push_back(static_cast<uint16_t>(bindings.size()));
                bindings.push_back({candidates[p].handle, static_cast<uint32_t>(i)});
            }
        }

        size_t append_bank = (base_rom.size() + 0x3FFF) / 0x4000;
        size_t append_cursor = 0;
        bool appended = false;
        int32_t owner = 0;
        for (size_t p = 0; p < candidates.size(); ++p) {
            const Package& package = candidates[p];
            section_addresses[p].reserve(package.sections.size());
            for (const Section& section : package.sections) {
                Address address;
                if (section.data.size() > 0x4000)
                    return fail(Status::link_error, "section '" + section.name + "' is larger than one ROM bank");
                if (section.placement == placement_fixed) {
                    if (!make_address(section.bank, section.address, address))
                        return fail(Status::link_error, "section '" + section.name + "' has an invalid fixed address");
                } else if (section.placement == placement_symbol) {
                    Status status = resolve_named(section.anchor, symbols, address);
                    if (status != Status::ok) return status;
                    Address advanced;
                    if (!advance_address(address, section.anchor_addend, advanced))
                        return fail(Status::link_error, "section '" + section.name + "' crosses a bank boundary");
                    address = advanced;
                } else {
                    appended = true;
                    append_cursor = (append_cursor + section.alignment - 1) & ~(section.alignment - 1);
                    if (append_cursor + section.data.size() > 0x4000) {
                        ++append_bank;
                        append_cursor = 0;
                    }
                    if (append_bank == 0 || append_bank > 0x1FF ||
                        !make_address(static_cast<uint16_t>(append_bank),
                                      static_cast<uint16_t>(0x4000 + append_cursor), address))
                        return fail(Status::link_error, "appended section cannot be addressed by the cartridge");
                    append_cursor += section.data.size();
                }

                Address last;
                if (!advance_address(address, static_cast<int64_t>(section.data.size()) - 1, last))
                    return fail(Status::link_error, "section '" + section.name + "' crosses a bank boundary");
                const size_t needed = last.offset + 1;
                if (section.placement != placement_append && needed > base_rom.size())
                    return fail(Status::link_error, "fixed section '" + section.name + "' lies outside the base ROM");
                if (needed > image.rom.size()) {
                    image.rom.resize(needed, 0xFF);
                    owners.resize(needed, -1);
                }
                Status status = reserve_range(owners, address.offset, section.data.size(),
                                              owner++, package, "module section");
                if (status != Status::ok) return status;
                if (section.flags & section_verify_fill)
                    for (size_t i = 0; i < section.data.size(); ++i)
                        if (image.rom[address.offset + i] != section.fill)
                            return fail(Status::conflict, "section '" + section.name +
                                        "' did not find its declared fill bytes");
                std::copy(section.data.begin(), section.data.end(), image.rom.begin() + address.offset);
                section_addresses[p].push_back(address);
            }
        }

        for (size_t p = 0; p < candidates.size(); ++p) {
            const Package& package = candidates[p];
            patch_addresses[p].reserve(package.patches.size());
            for (const Patch& patch : package.patches) {
                Address address;
                if (patch.target_kind == patch_fixed) {
                    if (!make_address(patch.bank, patch.address, address))
                        return fail(Status::link_error, "ROM patch in mod '" + package.id + "' has an invalid address");
                } else {
                    Status status = resolve_named(patch.target, symbols, address);
                    if (status != Status::ok) return status;
                    Address advanced;
                    if (!advance_address(address, patch.target_addend, advanced))
                        return fail(Status::link_error, "ROM patch in mod '" + package.id + "' crosses a bank boundary");
                    address = advanced;
                }
                Address last;
                if (!advance_address(address, static_cast<int64_t>(patch.data.size()) - 1, last) ||
                    last.offset >= base_rom.size())
                    return fail(Status::link_error, "ROM patch in mod '" + package.id + "' lies outside the base ROM");
                if (!patch.expected.empty() &&
                    !std::equal(patch.expected.begin(), patch.expected.end(), base_rom.begin() + address.offset))
                    return fail(Status::rom_mismatch, "ROM patch in mod '" + package.id + "' failed its expected-byte check");
                Status status = reserve_range(owners, address.offset, patch.data.size(), owner++, package, "ROM patch");
                if (status != Status::ok) return status;
                std::copy(patch.data.begin(), patch.data.end(), image.rom.begin() + address.offset);
                patch_addresses[p].push_back(address);
            }
        }

        for (size_t p = 0; p < candidates.size(); ++p) {
            const Package& package = candidates[p];
            export_addresses[p].reserve(package.symbols.size());
            for (const ModuleSymbol& symbol : package.symbols) {
                Address address;
                if (!advance_address(section_addresses[p][symbol.section], symbol.offset, address))
                    return fail(Status::link_error, "module export '" + symbol.name + "' crosses a bank boundary");
                export_addresses[p].push_back(address);
            }
        }

        for (size_t p = 0; p < candidates.size(); ++p) {
            const Package& package = candidates[p];
            for (const Relocation& relocation : package.relocations) {
                const Address& target_base = relocation.target_kind == relocation_section
                    ? section_addresses[p][relocation.target_index]
                    : patch_addresses[p][relocation.target_index];
                Address target;
                if (!advance_address(target_base, relocation.offset, target))
                    return fail(Status::link_error, "relocation target crosses a bank boundary in mod '" + package.id + "'");

                Address reference;
                uint16_t slot = 0;
                if (relocation.reference_kind == reference_rom) {
                    Status status = resolve_named(relocation.rom_symbol, symbols, reference);
                    if (status != Status::ok) return status;
                } else if (relocation.reference_kind == reference_module) {
                    reference = export_addresses[p][relocation.reference];
                } else {
                    slot = import_slots[p][relocation.reference];
                }

                Address adjusted;
                if (relocation.reference_kind != reference_host &&
                    !advance_address(reference, relocation.addend, adjusted))
                    return fail(Status::link_error, "relocation reference crosses a bank boundary in mod '" + package.id + "'");

                switch (relocation.type) {
                    case reloc_abs8:
                        if (relocation.reference_kind == reference_host || adjusted.address > 0xFF)
                            return fail(Status::link_error, "ABS8 relocation is out of range in mod '" + package.id + "'");
                        image.rom[target.offset] = static_cast<uint8_t>(adjusted.address);
                        break;
                    case reloc_abs16:
                        if (relocation.reference_kind == reference_host)
                            return fail(Status::link_error, "ABS16 relocation references a host import");
                        write_u16(image.rom, target.offset, adjusted.address);
                        break;
                    case reloc_bank8: {
                        const int64_t bank = static_cast<int64_t>(reference.bank) + relocation.addend;
                        if (relocation.reference_kind == reference_host || bank < 0 || bank > 0xFF)
                            return fail(Status::link_error, "BANK8 relocation is out of range in mod '" + package.id + "'");
                        image.rom[target.offset] = static_cast<uint8_t>(bank);
                        break;
                    }
                    case reloc_bank16: {
                        const int64_t bank = static_cast<int64_t>(reference.bank) + relocation.addend;
                        if (relocation.reference_kind == reference_host || bank < 0 || bank > 0x1FF)
                            return fail(Status::link_error, "BANK16 relocation is out of range in mod '" + package.id + "'");
                        write_u16(image.rom, target.offset, static_cast<uint16_t>(bank));
                        break;
                    }
                    case reloc_relative8: {
                        if (relocation.reference_kind == reference_host || adjusted.bank != target.bank)
                            return fail(Status::link_error, "REL8 relocation crosses a bank in mod '" + package.id + "'");
                        const int64_t delta = static_cast<int64_t>(adjusted.address) - target.address - 1;
                        if (delta < -128 || delta > 127)
                            return fail(Status::link_error, "REL8 relocation is out of range in mod '" + package.id + "'");
                        image.rom[target.offset] = static_cast<uint8_t>(static_cast<int8_t>(delta));
                        break;
                    }
                    case reloc_call16:
                        if (relocation.reference_kind == reference_host || target.offset == 0 ||
                            !is_call_opcode(image.rom[target.offset - 1]))
                            return fail(Status::link_error, "CALL16 relocation is not attached to a CALL instruction");
                        if (adjusted.bank != 0 && adjusted.bank != target.bank)
                            return fail(Status::link_error, "CALL16 relocation crosses a switched ROM bank in mod '" + package.id + "'");
                        write_u16(image.rom, target.offset, adjusted.address);
                        break;
                    case reloc_host16:
                        if (relocation.reference_kind != reference_host || relocation.addend != 0 ||
                            target.offset == 0 || image.rom[target.offset - 1] != 0xD3)
                            return fail(Status::link_error, "HOST16 relocation is not attached to a D3 host-call trap");
                        write_u16(image.rom, target.offset, slot);
                        image.traps.emplace(target.offset - 1, bindings[slot]);
                        break;
                    default:
                        return fail(Status::bad_package, "unknown relocation type");
                }
            }
        }

        if (appended) {
            uint32_t used_banks = static_cast<uint32_t>((image.rom.size() + 0x3FFF) / 0x4000);
            uint32_t padded_banks = 2;
            while (padded_banks < used_banks && padded_banks < 512) padded_banks <<= 1;
            const uint32_t maximum = max_rom_banks(base_rom[0x147]);
            if (maximum == 0 || padded_banks > maximum)
                return fail(Status::link_error, "appended sections exceed the cartridge mapper's ROM-bank limit");
            image.rom.resize(static_cast<size_t>(padded_banks) * 0x4000, 0xFF);
            uint8_t size_code = 0;
            for (uint32_t banks = 2; banks < padded_banks; banks <<= 1) ++size_code;
            image.rom[0x148] = size_code;
        }
        if (image.rom[0x147] != base_rom[0x147] || image.rom[0x149] != base_rom[0x149] ||
            !appended && image.rom[0x148] != base_rom[0x148])
            return fail(Status::link_error, "mods may not change the cartridge mapper/RAM/ROM-size header fields");
        update_checksums(image.rom);
        return Status::ok;
    }

    void commit(LinkImage&& image) {
        cartridge->rom = std::move(image.rom);
        traps = std::move(image.traps);
        error.clear();
    }
