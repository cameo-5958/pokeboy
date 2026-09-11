#pragma once
#include <string>
#include "mask.h"
#include "rng.h"
#include "features.h"
#include "model.h"
namespace pkai {
int64_t monotonic_ns();
class Scheduler {
public:
    // Stage values are part of the save-state stream; new stages append after Ready.
    // Order of execution: Events, Read, Masking, [Featurize, Infer], SampleAction, Write, Ready.
    enum Stage : uint8_t { Idle, Events, Read, Masking, SampleAction, Write, Ready, Featurize, Infer, StageCount };
    struct Identity {
        uint32_t sequence{}, reserved_draw{}, observation_hash{};
        uint64_t first_frame{}, last_frame{};
        uint16_t visible_frames{};
        uint8_t kind{};
        Action result{};
        void serialize(StateIO&);
    } request;
    Stage stage=Idle;
    uint64_t completions{}, timeouts{}, model_decisions{};
    uint64_t cost_ns[StageCount]{}, op_cost_ns[OpKindCount]{}, compute_steps{};
    Scheduler();
    void reset();
    uint8_t poll(const Memory&,Tracker&,uint8_t kind,uint64_t frame);
    void step(const Memory&,Tracker&,int64_t absolute_deadline);
    bool pending() const {return stage!=Idle && stage!=Ready;}
    void serialize(StateIO&);
    // Model backend. Until weights are loaded (or if loading fails) decisions come
    // from random_action. Loading calibrates the per-op-kind costs used by step().
    bool load_weights(const char* path);
    bool model_loaded() const {return model.bound();}
    const std::string& weights_error() const {return weights_error_;}
    Rng rng;
    Weights weights;
    PepModel model;
    const Features& features() const {return features_;}
private:
    Observation observation{};
    Mask mask{};
    Features features_{};
    bool inferred=false;   // the current request's result came from the model (not serialized: a
                           // pending decision restarts from Events on load, see serialize()).
    std::string weights_error_;
    void publish(const Memory&,Tracker&);
};
}
