#include "scheduler.h"
#include <chrono>
#include <algorithm>
namespace pkai {
using namespace symbols;
static_assert(EVENT_DIM==EV_DIM,"event vector width");
int64_t monotonic_ns() { return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
Scheduler::Scheduler() {
    // Calibrate the fixed stages on a blank machine. Model op costs are calibrated
    // when weights are loaded (load_weights) and tracked per op kind afterwards.
    std::array<uint8_t,65536> bytes{};
    Memory m{bytes.data(), [](void* p,uint16_t a){return static_cast<uint8_t*>(p)[a];},
        [](void* p,uint16_t a,uint8_t v){static_cast<uint8_t*>(p)[a]=v;},nullptr,0};
    Tracker t; Observation o; Mask legal; Features F; int8_t ev[EVENT_DIM];
    volatile uint32_t sink=0;
    for(Stage stage_index:{Events,Read,Masking,Featurize,SampleAction,Write}) {
        uint64_t total=0;
        for(unsigned i=0;i<32;++i) {
            t.pending=1;
            auto start=monotonic_ns();
            switch(stage_index) {
            case Events:t.drain_one();sink=t.pending;break;
            case Read:o=observe(m,t);sink=o.active.hp;break;
            case Masking:legal=legal_mask(m,o,0);sink=legal.bits;break;
            case Featurize:F=build_features(m,o,legal,0);build_event(t.event,m,ev);sink=F.legal^ev[15];break;
            case SampleAction:sink=random_action(legal,i,0).payload;break;
            case Write: {
                auto current=observe(m,t);auto mask=legal_mask(m,current,0);
                auto a=random_action(mask,i,0);m.write(wAIAction,a.kind);
                m.write(wAIAction+1,a.payload);m.write(wAIAction+2,a.sequence);
                m.write(wEnemyMoveListIndex,a.payload);m.write(wEnemySelectedMove,0xa5);
                sink=a.payload;break;
            }
            default:break;
            }
            total+=std::max<int64_t>(1,monotonic_ns()-start);
        }
        cost_ns[stage_index]=std::max<uint64_t>(1000,total/32);
    }
    (void)sink;
}
bool Scheduler::load_weights(const char* path) {
    weights_error_.clear();
    model=PepModel{}; inferred=false;
    if(!weights.load(path)) { weights_error_=weights.error(); return false; }
    if(!model.bind(weights)) { weights_error_=model.error(); weights.clear(); model=PepModel{}; return false; }
    // Seed the per-kind op costs with the slowest instance of each kind in one
    // blank decision; step() then tracks them with the usual EMA.
    Features F{}; F.count=MAX_TOKENS; F.legal=0xffff; int16_t h[128]{};
    for(auto& c:op_cost_ns) c=0;
    model.begin(F,nullptr,h);
    while(!model.done()) {
        auto kind=model.next_kind(); auto start=monotonic_ns(); model.step();
        op_cost_ns[kind]=std::max<uint64_t>(op_cost_ns[kind],uint64_t(std::max<int64_t>(1,monotonic_ns()-start)));
    }
    for(auto& c:op_cost_ns) c=std::max<uint64_t>(1000,c);
    return true;
}
void Scheduler::reset() { stage=Idle;request={};rng.seed(1);completions=timeouts=compute_steps=model_decisions=0;model.cancel();inferred=false; }
void Scheduler::Identity::serialize(StateIO& s) {
    s.v(sequence);s.v(reserved_draw);s.v(observation_hash);s.v(first_frame);s.v(last_frame);
    s.v(visible_frames);s.v(kind);result.serialize(s);
}
static uint32_t hash_observation(const Observation& o) {
    uint32_t h=2166136261u;
    auto add=[&](uint32_t x){h=(h^x)*16777619u;};
    add(o.round);add(o.own_slot);add(o.trainer_class);add(o.count);add(o.disabled);
    for(auto& p:o.own) {add(p.species);add(p.hp);add(p.status);for(auto v:p.moves)add(v);}
    for(auto& p:o.player) {add(p.known);add(p.species);add(p.hp);add(p.status);for(auto v:p.moves)add(v);}
    return h;
}
void Scheduler::publish(const Memory& m,Tracker& t) {
    auto current=observe(m,t); auto legal=legal_mask(m,current,request.kind);
    if(!legal_action(legal,request.result)) request.result=random_action(legal,request.reserved_draw,uint8_t(request.sequence));
    mask=legal;
    if(legal_action(mask,request.result)) {
        m.write(wAIAction,request.result.kind);m.write(wAIAction+1,request.result.payload);m.write(wAIAction+2,request.result.sequence);
        if(request.result.kind==0) {
            m.write(wEnemyMoveListIndex,request.result.payload);
            m.write(wEnemySelectedMove,mask.struggle?0xa5:current.active.moves[request.result.payload]);
        }
    }
    stage=Ready;
}
uint8_t Scheduler::poll(const Memory& m,Tracker& t,uint8_t kind,uint64_t frame) {
    if(kind>1) return 2;
    if(stage==Idle) {
        ++request.sequence;request.kind=kind;request.reserved_draw=rng.next();
        request.first_frame=request.last_frame=frame;request.visible_frames=0;request.result={};
        request.observation_hash=hash_observation(observe(m,t));stage=Events;inferred=false;
    } else if(request.kind!=kind) return 2;
    if(frame!=request.last_frame) {
        auto delta=frame>=request.last_frame?frame-request.last_frame:0;
        request.visible_frames=uint16_t(std::min<uint64_t>(300,request.visible_frames+delta));request.last_frame=frame;
    }
    if(stage!=Ready && request.visible_frames>=300) {
        // Timeout: random legal action; an unfinished model decision is abandoned and
        // the GRU hidden state is left as it was before this request.
        model.cancel();inferred=false;
        mask=legal_mask(m,observe(m,t),kind);request.result=random_action(mask,request.reserved_draw,uint8_t(request.sequence));
        publish(m,t);++timeouts;
    }
    if(stage==Ready) {
        publish(m,t); // Restore READY is transactional, including action bytes.
        // The decision is final: snapshot for the next event vector. RAM has not
        // changed since Write (the ROM only polls), so a state saved at Ready commits
        // the same snapshot on load.
        t.event.commit(m,request.result.kind);
        stage=Idle;++completions;
        return legal_action(mask,request.result)?1:2;
    }
    return 0;
}
void Scheduler::step(const Memory& m,Tracker& t,int64_t deadline) {
    while(pending()) {
        auto start=monotonic_ns(); auto index=stage;
        const auto kind=stage==Infer?model.next_kind():OpPass;
        const uint64_t cost=stage==Infer?op_cost_ns[kind]:cost_ns[index];
        if(deadline<=start || uint64_t(deadline-start)<cost+5000) return;
        switch(stage) {
        case Events: if(t.pending) t.drain_one();else stage=Read;break;
        case Read: observation=observe(m,t);stage=Masking;break;
        case Masking: mask=legal_mask(m,observation,request.kind);stage=model.bound()?Featurize:SampleAction;break;
        case Featurize:
            // GRU input is [event ⊕ c_s]. The event vector compares RAM now with the
            // snapshot taken when the previous decision was published (tracker.event,
            // committed in poll); it and the hidden state persist across the decisions
            // of one battle, are zeroed by $DB (tracker.reset) and are in the save state.
            features_=build_features(m,observation,mask,request.kind);
            build_event(t.event,m,event.data());
            model.begin(features_,event.data(),t.hidden.data());stage=Infer;break;
        case Infer: if(model.step()) {inferred=true;stage=SampleAction;}break;
        case SampleAction:
            request.result=inferred?sample_action(mask,model.probs(),request.reserved_draw,uint8_t(request.sequence))
                                  :random_action(mask,request.reserved_draw,uint8_t(request.sequence));
            stage=Write;break;
        case Write:
            // Commit the new hidden state only now: a state saved before this point
            // restarts the decision from Events on load and recomputes the same h.
            if(inferred) {std::copy(model.hidden(),model.hidden()+model.gru_size(),t.hidden.begin());++model_decisions;}
            publish(m,t);break;
        default:return;
        }
        auto duration=std::max<int64_t>(1,monotonic_ns()-start);
        if(index==Infer) op_cost_ns[kind]=(7*op_cost_ns[kind]+duration)/8; else cost_ns[index]=(7*cost_ns[index]+duration)/8;
        ++compute_steps;
    }
}
void Scheduler::serialize(StateIO& s) {
    s.v(stage);request.serialize(s);rng.serialize(s);s.v(completions);s.v(timeouts);
    if(stage>=StageCount || request.kind>1 || request.visible_frames>300) s.fail();
    // A decision in flight is not serialized (model scratch, features, mask): it
    // restarts from Events on load. Its inputs (RAM, tracker, hidden) are all in
    // the stream and the RNG draw is reserved, so the restarted decision is the same.
    if(!s.saving() && pending()) {stage=Events;model.cancel();inferred=false;}
}
}
