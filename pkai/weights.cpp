#include "weights.h"
#include "hook.h"
#include <cstdio>
#include <cstring>
namespace pkai {
namespace {
constexpr size_t HEADER_SIZE = 128, TOC_SIZE = 80, ALIGN = 64;
constexpr uint32_t VERSION = 1;
uint32_t rd32(const uint8_t* p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }
}

void Weights::clear() {
    entries_.clear(); storage_.reset(); base_ = nullptr; size_ = 0; config_ = {};
    std::memset(tier_, 0, sizeof tier_); std::memset(schema_, 0, sizeof schema_); rom_crc32_ = 0;
}

bool Weights::load(const char* path) {
    clear();
    FILE* f = std::fopen(path, "rb");
    if (!f) return fail("cannot open weights file");
    std::fseek(f, 0, SEEK_END); long n = std::ftell(f); std::fseek(f, 0, SEEK_SET);
    if (n < long(HEADER_SIZE)) { std::fclose(f); return fail("weights file too small"); }
    std::vector<uint8_t> bytes(static_cast<size_t>(n), uint8_t(0));
    size_t got = std::fread(bytes.data(), 1, bytes.size(), f);
    std::fclose(f);
    if (got != bytes.size()) return fail("short read");
    return load_from_memory(bytes.data(), bytes.size());
}

bool Weights::load_from_memory(const uint8_t* bytes, size_t len) {
    clear();
    if (len < HEADER_SIZE) return fail("weights file too small");
    if (std::memcmp(bytes, "PKAI", 4) != 0) return fail("bad magic");
    if (rd32(bytes + 4) != VERSION) return fail("unsupported version");
    const uint32_t n_tensors = rd32(bytes + 84), toc_offset = rd32(bytes + 88), data_offset = rd32(bytes + 92), file_size = rd32(bytes + 96);
    if (file_size != len) return fail("file size mismatch");
    if (toc_offset != HEADER_SIZE || n_tensors == 0 || n_tensors > 4096) return fail("bad table of contents");
    if (size_t(toc_offset) + size_t(n_tensors) * TOC_SIZE > data_offset || data_offset > len || data_offset % ALIGN) return fail("bad data offset");
    std::memcpy(tier_, bytes + 8, 15);
    config_.d = rd32(bytes + 24); config_.layers = rd32(bytes + 28); config_.heads = rd32(bytes + 32);
    config_.ffn = rd32(bytes + 36); config_.gru = rd32(bytes + 40);
    std::memcpy(schema_, bytes + 44, 31);
    if (crc32(reinterpret_cast<const uint8_t*>(schema_), std::strlen(schema_)) != rd32(bytes + 76)) return fail("feature schema crc mismatch");
    rom_crc32_ = rd32(bytes + 80);
    config_.emb_species = rd32(bytes + 100); config_.emb_move = rd32(bytes + 104);
    config_.emb_matchup = rd32(bytes + 108); config_.emb_small = rd32(bytes + 112);
    if (!config_.d || !config_.layers || !config_.heads || !config_.ffn || !config_.gru || config_.d % config_.heads) return fail("bad model config");

    // Aligned copy so that every 64-byte aligned file offset stays aligned in memory.
    storage_.reset(new uint8_t[len + ALIGN]);
    base_ = storage_.get();
    base_ += (ALIGN - (reinterpret_cast<uintptr_t>(base_) % ALIGN)) % ALIGN;
    std::memcpy(base_, bytes, len);
    size_ = len;

    entries_.reserve(n_tensors);
    for (uint32_t i = 0; i < n_tensors; ++i) {
        const uint8_t* e = base_ + toc_offset + size_t(i) * TOC_SIZE;
        Entry en{};
        std::memcpy(en.name, e, 47);
        Tensor& t = en.tensor;
        const uint8_t dtype = e[48];
        if (dtype > 7) return fail("bad tensor dtype");
        t.dtype = Dtype(dtype); t.ndim = e[49];
        if (t.ndim > 4) return fail("bad tensor rank");
        for (unsigned k = 0; k < 4; ++k) t.shape[k] = rd32(e + 52 + 4 * k);
        const uint32_t offset = rd32(e + 68); t.nbytes = rd32(e + 72);
        if (offset < data_offset || offset % ALIGN || size_t(offset) + t.nbytes > len) return fail("tensor outside file");
        if (t.count() * dtype_size(t.dtype) != t.nbytes) return fail("tensor shape/size mismatch");
        t.data = base_ + offset;
        entries_.push_back(en);
    }
    return true;
}

Tensor Weights::find(const char* name) const {
    for (const auto& e : entries_) if (std::strncmp(e.name, name, 47) == 0) return e.tensor;
    return Tensor{};
}

}
