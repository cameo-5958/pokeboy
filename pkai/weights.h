#pragma once
// pkai.weights container (spec §9.4, ai/models/pep_weights.py): 128-byte header,
// 80-byte TOC entries, 64-byte aligned raw tensors. The whole file is read once
// into 64-byte aligned storage; tensors are views into that buffer (every tensor
// offset in the file is a multiple of 64, which the loader verifies, so int16 and
// int32 views are always naturally aligned). No allocation after load().
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>
namespace pkai {

enum class Dtype : uint8_t { I8 = 0, U8 = 1, I16 = 2, U16 = 3, I32 = 4, U32 = 5, F32 = 6, F64 = 7 };
inline unsigned dtype_size(Dtype d) { static const unsigned s[8] = {1, 1, 2, 2, 4, 4, 4, 8}; return s[unsigned(d) & 7]; }

struct Tensor {
    const void* data{};
    Dtype dtype{};
    uint8_t ndim{};
    uint32_t shape[4]{};
    uint32_t nbytes{};
    size_t count() const { size_t n = 1; for (unsigned i = 0; i < ndim; ++i) n *= shape[i]; return ndim ? n : 0; }
    const int8_t* i8() const { return dtype == Dtype::I8 ? static_cast<const int8_t*>(data) : nullptr; }
    const int16_t* i16() const { return dtype == Dtype::I16 ? static_cast<const int16_t*>(data) : nullptr; }
    const int32_t* i32() const { return dtype == Dtype::I32 ? static_cast<const int32_t*>(data) : nullptr; }
    explicit operator bool() const { return data != nullptr; }
};

struct WeightsConfig {
    uint32_t d{}, layers{}, heads{}, ffn{}, gru{};
    uint32_t emb_species{}, emb_move{}, emb_matchup{}, emb_small{};
};

class Weights {
public:
    // Reads and validates a pkai.weights file. On failure returns false and
    // `error()` describes why; the object is then empty.
    bool load(const char* path);
    bool load_from_memory(const uint8_t* bytes, size_t len);
    bool loaded() const { return !entries_.empty(); }
    const std::string& error() const { return error_; }
    const WeightsConfig& config() const { return config_; }
    // 16-byte model identifier from the header ("pep"); informational only, never validated.
    const char* model_id() const { return model_id_; }
    const char* feature_schema() const { return schema_; }
    uint32_t rom_crc32() const { return rom_crc32_; }
    size_t tensor_count() const { return entries_.size(); }
    // Lookup by exact name; an empty Tensor (data == nullptr) when absent.
    Tensor find(const char* name) const;
    const Tensor& at(size_t i) const { return entries_[i].tensor; }
    const char* name_at(size_t i) const { return entries_[i].name; }
    void clear();
private:
    struct Entry { char name[48]; Tensor tensor; };
    std::unique_ptr<uint8_t[]> storage_;   // 64-byte aligned copy of the file
    uint8_t* base_{};
    size_t size_{};
    std::vector<Entry> entries_;
    WeightsConfig config_{};
    char model_id_[16]{}, schema_[32]{};
    uint32_t rom_crc32_{};
    std::string error_;
    bool fail(const char* why) { clear(); error_ = why; return false; }
};

}
