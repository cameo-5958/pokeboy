(function(global){
  "use strict";

  global.createStorageSystem = function(options){
  /* ---------------- battery save ---------------- */
  function bytesToBase64(bytes){
    var CHUNK = 0x8000, parts = [];
    for(var i=0;i<bytes.length;i+=CHUNK){
      parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i+CHUNK)));
    }
    return btoa(parts.join(""));
  }
  function base64ToBytes(b64){
    var bin = atob(b64);
    var bytes = new Uint8Array(bin.length);
    for(var i=0;i<bin.length;i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  }

  function loadSaveIfPresent(){
    if(!options.hasBattery()) return;
    var b64 = localStorage.getItem(options.saveKey());
    if(!b64) return;
    try{
      var bytes = base64ToBytes(b64);
      var ptr = options.module()._malloc(bytes.length);
      options.module().HEAPU8.set(bytes, ptr);
      options.api.loadSaveRam(options.gb(), ptr, bytes.length);
      options.module()._free(ptr);
    }catch(err){
      console.warn("save load failed", err);
    }
  }

  /* ---------------- save states ---------------- */
  /* Distinct from the battery save above: that is only cartridge RAM, and only
     for carts that have it. A state is the whole machine, so it works on any
     cart and restores mid-battle rather than at the last in-game save. */
  var STATE_SLOTS = 3;
  var activeSlot = 1;
  var slotsEl = document.getElementById("slots");
  var btnSaveState = document.getElementById("btnSaveState");
  var btnLoadState = document.getElementById("btnLoadState");

  function stateKey(slot){
    if(!options.currentCart()) return null;
    return "gbemu.state." + options.currentCart().file.replace(/\.gb$/i, "") + "." + slot;
  }

  function slotFilled(slot){
    var k = stateKey(slot);
    return !!(k && localStorage.getItem(k));
  }

  function buildSlots(){
    slotsEl.innerHTML = "";
    for(var i = 1; i <= STATE_SLOTS; i++){
      (function(slot){
        var b = document.createElement("button");
        b.className = "slot-btn";
        b.textContent = String(slot);
        b.setAttribute("aria-label", "state slot " + slot);
        b.addEventListener("click", function(){
          options.audio.playClick("pill");
          activeSlot = slot;
          refreshSlots();
        });
        slotsEl.appendChild(b);
      })(i);
    }
    refreshSlots();
  }

  function refreshSlots(){
    var kids = slotsEl.children;
    for(var i = 0; i < kids.length; i++){
      var slot = i + 1;
      kids[i].setAttribute("aria-pressed", slot === activeSlot ? "true" : "false");
      kids[i].classList.toggle("filled", slotFilled(slot));
    }
    // Both actions need a running machine; LOAD additionally needs the slot to
    // hold something, so a click can never reach the core with nothing to read.
    var live = !!(options.gb() && options.currentCart());
    btnSaveState.disabled = !live;
    btnLoadState.disabled = !live || !slotFilled(activeSlot);
  }

  function saveStateToSlot(slot){
    if(!options.gb() || !options.currentCart()) return;
    var lenPtr = options.module()._malloc(4);
    options.module().HEAPU32[lenPtr/4] = 0;
    var ptr = options.api.saveState(options.gb(), lenPtr);
    var len = options.module().HEAPU32[lenPtr/4];
    options.module()._free(lenPtr);
    if(!ptr || !len){ flashState("SAVE FAILED"); return; }
    // Copy before anything else can call gb_save_state: the core hands back a
    // pointer into a buffer it owns and reuses.
    var bytes = options.module().HEAPU8.slice(ptr, ptr + len);
    try{
      localStorage.setItem(stateKey(slot), bytesToBase64(bytes));
      flashState("SAVED " + slot);
    }catch(err){
      // A state is far larger than a battery save, so localStorage quota is a
      // realistic failure here rather than a theoretical one.
      console.warn("state save failed", err);
      flashState("NO SPACE");
    }
    refreshSlots();
  }

  function loadStateFromSlot(slot){
    if(!options.gb() || !options.currentCart()) return;
    var b64 = localStorage.getItem(stateKey(slot));
    if(!b64){ flashState("SLOT " + slot + " EMPTY"); return; }
    var ok = 0;
    try{
      var bytes = base64ToBytes(b64);
      var ptr = options.module()._malloc(bytes.length);
      options.module().HEAPU8.set(bytes, ptr);
      ok = options.api.loadState(options.gb(), ptr, bytes.length);
      options.module()._free(ptr);
    }catch(err){
      console.warn("state load failed", err);
    }
    // The core refuses a state from different ROM bytes and leaves itself
    // untouched, so a stale slot is reported rather than silently ignored.
    flashState(ok ? "LOADED " + slot : "SLOT " + slot + " STALE");
  }

  var stateFlashTimer = 0;
  function flashState(text){
    var label = document.querySelector("#state .vol-label");
    if(!label) return;
    label.textContent = text;
    clearTimeout(stateFlashTimer);
    stateFlashTimer = setTimeout(function(){ label.textContent = "STATE"; }, 1200);
  }

  btnSaveState.addEventListener("click", function(){
    options.audio.playClick("pill");
    saveStateToSlot(activeSlot);
  });
  btnLoadState.addEventListener("click", function(){
    options.audio.playClick("pill");
    loadStateFromSlot(activeSlot);
  });

  function persistSave(){
    if(!options.hasBattery() || !options.gb()) return;
    var lenPtr = options.module()._malloc(4);
    options.module().HEAPU32[lenPtr/4] = 0;
    var ptr = options.api.saveRam(options.gb(), lenPtr);
    var len = options.module().HEAPU32[lenPtr/4];
    options.module()._free(lenPtr);
    if(!ptr || !len) return;
    var bytes = options.module().HEAPU8.subarray(ptr, ptr+len);
    try{
      localStorage.setItem(options.saveKey(), bytesToBase64(bytes));
    }catch(err){
      console.warn("save persist failed", err);
    }
  }

  setInterval(persistSave, options.saveIntervalMs);
  document.addEventListener("visibilitychange", function(){
    if(document.visibilityState === "hidden") persistSave();
  });


    return {
      slotCount: STATE_SLOTS,
      buildSlots: buildSlots,
      refreshSlots: refreshSlots,
      loadSaveIfPresent: loadSaveIfPresent,
      persistSave: persistSave,
      selectSlot: function(slot){ activeSlot = slot; refreshSlots(); },
      saveActive: function(){ saveStateToSlot(activeSlot); },
      loadActive: function(){ loadStateFromSlot(activeSlot); }
    };
  };
})(window);
