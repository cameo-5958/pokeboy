(function(){
  "use strict";

  /* ---------------- constants ---------------- */
  var W = 160, H = 144;
  var FRAME_MS = 1000 / 59.7275;
  var MAX_CATCHUP_FRAMES = 4;
  var BTN = { A:0x01, B:0x02, SELECT:0x04, START:0x08 };
  var DPAD = { RIGHT:0x01, LEFT:0x02, UP:0x04, DOWN:0x08 };
  var SAVE_INTERVAL_MS = 10000;
  // cartridge library: overridden by carts.json if present
  var CARTS = [{ id:"pokemon-red", file:"pokemon-red.gb", title:"Pokémon Red" }];
  var currentCart = null;
  function saveKey(){
    return "gbemu.save." + currentCart.file.replace(/\.gb$/i, "");
  }

  var overlay = document.getElementById("overlay");
  var ovTitle = document.getElementById("ovTitle");
  var ovText = document.getElementById("ovText");
  var ovButton = document.getElementById("ovButton");
  var ovSpinner = document.getElementById("ovSpinner");
  var powerDot = document.getElementById("powerDot");

  function showError(title, text){
    overlay.classList.remove("hidden");
    overlay.classList.add("error");
    ovSpinner.style.display = "none";
    ovButton.style.display = "none";
    ovTitle.textContent = title;
    ovText.textContent = text;
  }

  /* ---------------- state ---------------- */
  var Module = null;
  var gb = 0;
  var api = {};
  var fbPtr = 0;
  var audioPtr = 0;
  var AUDIO_MAX_FRAMES = 4096; // stereo frames per read
  var started = false;
  var hasBattery = false;

  var canvas = document.getElementById("screen");

  /* size the canvas to fill the screen area, preserving 10:9 */
  (function(){
    var wrap = document.getElementById("screen-wrap");
    var bezel = document.getElementById("bezel");
    function fit(){
      var cs = getComputedStyle(bezel);
      var padW = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
      var padH = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
      var availW = wrap.clientWidth - padW;
      var availH = wrap.clientHeight - padH;
      var scale = Math.max(1, Math.min(availW / W, availH / H));
      canvas.style.width = Math.floor(W * scale) + "px";
      canvas.style.height = Math.floor(H * scale) + "px";
    }
    fit();
    window.addEventListener("resize", fit);
    window.addEventListener("orientationchange", function(){ setTimeout(fit, 60); });
    if(window.ResizeObserver) new ResizeObserver(fit).observe(wrap);
  })();

  var ctx2d = canvas.getContext("2d", { alpha:false });
  var imageData = ctx2d.createImageData(W, H);
  var palette = [
    [0xc8,0xd4,0xa4],
    [0x8f,0xa8,0x78],
    [0x4e,0x63,0x50],
    [0x1c,0x2b,0x22]
  ];
  // pre-fill alpha channel
  (function(){
    var d = imageData.data;
    for(var i=3;i<d.length;i+=4) d[i]=255;
  })();

  var InputControls = createInputControls({
    BTN: BTN,
    DPAD: DPAD,
    api: api,
    gb: function(){ return gb; },
    audio: function(){ return AudioSystem; },
    stateSlotCount: 3,
    selectStateSlot: function(slot){ StorageSystem.selectSlot(slot); },
    saveState: function(){ StorageSystem.saveActive(); },
    loadState: function(){ StorageSystem.loadActive(); }
  });

  var AudioSystem = createAudioSystem();

  /* ---------------- render + loop ---------------- */
  function renderFrame(){
    var ptr = fbPtr;
    var src = Module.HEAPU8.subarray(ptr, ptr + W*H);
    var data = imageData.data;
    for(var i=0;i<W*H;i++){
      var shade = src[i] & 3;
      var c = palette[shade];
      var o = i*4;
      data[o] = c[0]; data[o+1] = c[1]; data[o+2] = c[2];
    }
    ctx2d.putImageData(imageData, 0, 0);
  }

  var audioBufBytes = AUDIO_MAX_FRAMES * 2 * 4;
  function pumpAudio(){
    var n = api.readAudio(gb, audioPtr, AUDIO_MAX_FRAMES);
    if(n <= 0) return;
    if(speed !== 1) return; // off-speed: drain the core's buffer but stay silent
    var interleaved = Module.HEAPF32.subarray(audioPtr/4, audioPtr/4 + n*2);
    var L = new Float32Array(n), R = new Float32Array(n);
    for(var i=0;i<n;i++){ L[i] = interleaved[i*2]; R[i] = interleaved[i*2+1]; }
    AudioSystem.push(L, R, n);
  }

  var SPEEDS = [0.5, 1, 2, 5, Infinity];
  var speed = 1;

  var acc = 0, lastT = 0, rafId = 0;
  function loop(t){
    rafId = requestAnimationFrame(loop);
    if(!lastT) lastT = t;
    var dt = t - lastT;
    lastT = t;
    if(dt > 250) dt = 250; // tab was backgrounded; avoid a huge catch-up burst
    var frames = 0;
    if(speed === Infinity){
      // uncapped: run as many frames as fit in ~11ms, leaving time to render
      var budget = performance.now() + 11;
      while(performance.now() < budget){
        api.runFrame(gb);
        pumpAudio();
        frames++;
      }
      acc = 0;
    } else {
      acc += dt * speed;
      var cap = Math.max(MAX_CATCHUP_FRAMES, Math.ceil(speed) + 2);
      while(acc >= FRAME_MS && frames < cap){
        api.runFrame(gb);
        pumpAudio();
        acc -= FRAME_MS;
        frames++;
      }
      if(acc > FRAME_MS * cap) acc = FRAME_MS * cap;
    }
    if(frames > 0){
      renderFrame();
    }
  }

  /* speed slider: snaps at x0.5, x1, x2, x5, MAX */
  (function(){
    var slider = document.getElementById("speedSlider");
    var label = document.getElementById("speedVal");
    slider.addEventListener("input", function(){
      speed = SPEEDS[slider.value | 0];
      label.textContent = speed === Infinity ? "MAX" : ("x" + speed);
    });
  })();

  var StorageSystem = createStorageSystem({
    api: api,
    audio: AudioSystem,
    currentCart: function(){ return currentCart; },
    gb: function(){ return gb; },
    hasBattery: function(){ return hasBattery; },
    module: function(){ return Module; },
    saveIntervalMs: SAVE_INTERVAL_MS,
    saveKey: saveKey
  });

  /* ---------------- run control ---------------- */
  var running = false;
  function pauseEmulation(){
    if(!running) return;
    running = false;
    cancelAnimationFrame(rafId);
  }
  function resumeEmulation(){
    if(running || !started) return;
    running = true;
    lastT = 0; acc = 0;
    rafId = requestAnimationFrame(loop);
  }
  /* first cart insertion doubles as the iOS audio-unlock gesture */
  var audioInited = false;
  function ensureAudio(){
    if(audioInited) return;
    audioInited = true;
    AudioSystem.init().then(function(){
      return AudioSystem.resume();
    }).catch(function(err){
      console.warn("audio init failed", err);
    });
  }

  /* gameplay screenshots double as the cartridge label art */
  function captureThumb(){
    if(!currentCart || !running) return;
    try{
      var t = document.createElement("canvas");
      t.width = 80; t.height = 72;
      t.getContext("2d").drawImage(canvas, 0, 0, 80, 72);
      localStorage.setItem("gbemu.thumb." + currentCart.file, t.toDataURL("image/jpeg", 0.65));
    }catch(err){}
  }
  setInterval(captureThumb, 20000);

  /* ---------------- cartridge dock ---------------- */
  var cartDock = document.getElementById("cartDock");
  var cartTab = document.getElementById("cartTab");
  var cartTray = document.getElementById("cartTray");
  var cartTitle = document.getElementById("cartTitle");

  function refreshCarts(){
    return fetch("carts.json", { cache:"no-store" }).then(function(resp){
      return resp.ok ? resp.json() : null;
    }).catch(function(){ return null; }).then(function(list){
      if(Array.isArray(list) && list.length){
        var valid = list.filter(function(c){ return c && c.file && c.title; });
        if(valid.length) CARTS = valid;
      }
    });
  }

  function fetchRomInto(file){
    return fetch("roms/" + encodeURIComponent(file)).then(function(resp){
      if(!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.arrayBuffer();
    }).then(function(buf){
      var romBytes = new Uint8Array(buf);
      var romPtr = Module._malloc(romBytes.length);
      Module.HEAPU8.set(romBytes, romPtr);
      var ok = api.loadRom(gb, romPtr, romBytes.length);
      Module._free(romPtr);
      if(!ok) throw new Error("gb_load_rom rejected " + file);
    });
  }

  var animTimer = 0;
  function ejectCart(){
    captureThumb();
    StorageSystem.persistSave();
    pauseEmulation();
    currentCart = null;
    powerDot.classList.remove("playing");
    InputControls.reset();
    StorageSystem.refreshSlots();                    // no cart: both state actions go dead
    // powered-off LCD
    ctx2d.fillStyle = "rgb(" + palette[0].join(",") + ")";
    ctx2d.fillRect(0, 0, W, H);
    // console (cart aboard) moves down, THEN cart pops out, THEN library opens
    clearTimeout(animTimer);
    document.body.classList.add("nocart");
    animTimer = setTimeout(function(){
      cartTab.classList.add("out");
      animTimer = setTimeout(function(){
        renderTray();
        cartDock.classList.add("open");
        // pick up newly added carts without a page reload
        refreshCarts().then(renderTray);
      }, 300);
    }, 520);
  }

  function insertCart(cart){
    return fetchRomInto(cart.file).then(function(){
      currentCart = cart;
      cartTitle.textContent = cart.title;
      try{ localStorage.setItem("gbemu.lastCart", cart.file); }catch(err){}
      hasBattery = !!api.hasBattery(gb);
      StorageSystem.loadSaveIfPresent();
      StorageSystem.refreshSlots();                  // slot pips reflect the cart just loaded
      started = true;
      // library closes, cart slides INTO the slot, THEN console rises and powers on
      clearTimeout(animTimer);
      cartDock.classList.remove("open");
      animTimer = setTimeout(function(){
        cartTab.classList.remove("out");
        animTimer = setTimeout(function(){
          document.body.classList.remove("nocart");
          animTimer = setTimeout(function(){
            powerDot.classList.add("playing");
            resumeEmulation();
          }, 500);
        }, 320);
      }, 300);
    });
  }

  function cartImage(cart){
    if(cart.img) return cart.img;
    try{ return localStorage.getItem("gbemu.thumb." + cart.file); }catch(err){ return null; }
  }

  function renderTray(){
    cartTray.innerHTML = "";
    CARTS.forEach(function(cart){
      var item = document.createElement("button");
      item.className = "cart-item";
      item.innerHTML = '<span class="cart-grip"></span><span class="cart-sticker"><span class="cart-name"></span></span>';
      item.querySelector(".cart-name").textContent = cart.title;
      var src = cartImage(cart);
      if(src){
        var img = document.createElement("img");
        img.alt = "";
        img.src = src;
        item.querySelector(".cart-sticker").insertBefore(img, item.querySelector(".cart-name"));
      }
      item.addEventListener("pointerdown", function(e){
        startCartDrag(cart, item, e);
      });
      cartTray.appendChild(item);
    });
  }

  /* ---------------- cartridge dragging ----------------
     Grab a cart from the tray and drag it around; it tilts to face its
     direction of travel. Passing it over the slot opening inserts it. */
  var drag = null;

  function slotZone(){
    var r = document.querySelector(".cart-slot-lip").getBoundingClientRect();
    // generous hitbox around the thin lip
    return { left:r.left - 20, right:r.right + 20, top:r.top - 55, bottom:r.bottom + 45,
             cx:r.left + r.width / 2, cy:r.top + r.height / 2 };
  }

  function renderDrag(){
    drag.ghost.style.transform =
      "translate(" + drag.x + "px," + drag.y + "px) rotate(" + drag.rot + "deg)";
  }

  function startCartDrag(cart, item, e){
    if(drag || currentCart) return;
    ensureAudio();
    e.preventDefault();
    var rect = item.getBoundingClientRect();
    var ghost = item.cloneNode(true);
    ghost.classList.add("cart-ghost");
    ghost.classList.remove("drag-src");
    ghost.style.width = rect.width + "px";
    document.body.appendChild(ghost);
    item.classList.add("drag-src");
    document.body.classList.add("cart-dragging");
    drag = {
      cart:cart, item:item, ghost:ghost,
      x:rect.left, y:rect.top,
      offX:e.clientX - rect.left, offY:e.clientY - rect.top,
      rot:0, vx:0, vy:0, lastT:e.timeStamp, done:false
    };
    renderDrag();
    try{ item.setPointerCapture(e.pointerId); }catch(err){}
    item.addEventListener("pointermove", moveCartDrag);
    item.addEventListener("pointerup", releaseCartDrag);
    item.addEventListener("pointercancel", releaseCartDrag);
    requestAnimationFrame(dragTick);
  }

  /* runs every frame while dragging. Pendulum feel: the cart always wants
     to hang upright, and speed swings it toward the direction of travel —
     the faster it moves, the further from upright it tilts. */
  function dragTick(){
    if(!drag || drag.done) return;
    // no pointermove lately -> velocity bleeds off, cart rights itself
    if(performance.now() - drag.lastT > 60){ drag.vx *= 0.82; drag.vy *= 0.82; }
    var speed = Math.hypot(drag.vx, drag.vy); // px/ms
    var target = 0;
    if(speed > 0.02){
      var travel = Math.atan2(drag.vx, -drag.vy) * 180 / Math.PI; // -180..180
      // full alignment only at flick speed; capped so it never hangs upside down
      target = Math.max(-120, Math.min(120, travel * Math.min(1, speed / 0.9)));
    }
    var diff = ((target - drag.rot + 540) % 360) - 180;
    drag.rot += diff * 0.18;
    renderDrag();
    requestAnimationFrame(dragTick);
  }

  function detachDragListeners(d){
    d.item.removeEventListener("pointermove", moveCartDrag);
    d.item.removeEventListener("pointerup", releaseCartDrag);
    d.item.removeEventListener("pointercancel", releaseCartDrag);
    document.body.classList.remove("cart-dragging");
  }

  function moveCartDrag(e){
    if(!drag || drag.done) return;
    var nx = e.clientX - drag.offX, ny = e.clientY - drag.offY;
    var dt = Math.max(1, e.timeStamp - drag.lastT);
    // smoothed velocity (px/ms)
    drag.vx += ((nx - drag.x) / dt - drag.vx) * 0.3;
    drag.vy += ((ny - drag.y) / dt - drag.vy) * 0.3;
    drag.x = nx; drag.y = ny; drag.lastT = e.timeStamp;
    renderDrag();

    // cart center over the slot opening -> it goes in
    var z = slotZone();
    var g = drag.ghost;
    var cx = drag.x + g.offsetWidth / 2, cy = drag.y + g.offsetHeight / 2;
    if(cx > z.left && cx < z.right && cy > z.top && cy < z.bottom){
      dropCartIn(z);
    }
  }

  function dropCartIn(z){
    var d = drag;
    d.done = true;
    drag = null;
    detachDragListeners(d);
    var g = d.ghost;
    // straighten out and sink into the slot
    g.style.transition = "transform .3s cubic-bezier(.4,.8,.4,1), opacity .2s ease .15s";
    g.style.opacity = "0";
    g.style.transform = "translate(" + (z.cx - g.offsetWidth / 2) + "px," +
      (z.cy - g.offsetHeight / 3) + "px) rotate(0deg) scale(.72)";
    AudioSystem.playClick("btn");
    setTimeout(function(){ g.remove(); }, 380);
    insertCart(d.cart).then(function(){
      d.item.classList.remove("drag-src");
    }).catch(function(err){
      console.warn("cart load failed", err);
      d.item.classList.remove("drag-src");
      d.item.classList.add("err");
      setTimeout(function(){ d.item.classList.remove("err"); }, 900);
    });
  }

  function releaseCartDrag(){
    if(!drag || drag.done) return;
    var d = drag;
    drag = null;
    detachDragListeners(d);
    // float back to its spot in the tray
    var home = d.item.getBoundingClientRect();
    var g = d.ghost;
    g.style.transition = "transform .28s cubic-bezier(.3,1.3,.5,1)";
    g.style.transform = "translate(" + home.left + "px," + home.top + "px) rotate(0deg)";
    setTimeout(function(){
      g.remove();
      d.item.classList.remove("drag-src");
    }, 300);
  }

  cartTab.addEventListener("click", function(){
    if(!gb || !currentCart) return; // not booted / already empty
    AudioSystem.playClick("pill");
    ejectCart();
  });

  function boot(){
    if(typeof createGBCore !== "function"){
      showError("Core missing", "gbcore.js did not define createGBCore(). Make sure gbcore.js and gbcore.wasm sit next to index.html.");
      return;
    }
    createGBCore().then(function(mod){
      Module = mod;
      api.create = Module.cwrap("gb_create", "number", []);
      api.loadRom = Module.cwrap("gb_load_rom", "number", ["number","number","number"]);
      api.runFrame = Module.cwrap("gb_run_frame", null, ["number"]);
      api.framebuffer = Module.cwrap("gb_framebuffer", "number", ["number"]);
      api.setInput = Module.cwrap("gb_set_input", null, ["number","number","number"]);
      api.readAudio = Module.cwrap("gb_read_audio", "number", ["number","number","number"]);
      api.hasBattery = Module.cwrap("gb_has_battery", "number", ["number"]);
      api.saveRam = Module.cwrap("gb_save_ram", "number", ["number","number"]);
      api.loadSaveRam = Module.cwrap("gb_load_save_ram", "number", ["number","number","number"]);
      api.saveState = Module.cwrap("gb_save_state", "number", ["number","number"]);
      api.loadState = Module.cwrap("gb_load_state", "number", ["number","number","number"]);

      return refreshCarts().then(function(){
        gb = api.create();
        if(!gb){ throw new Error("gb_create returned null"); }
        fbPtr = api.framebuffer(gb);
        audioPtr = Module._malloc(audioBufBytes);
        StorageSystem.buildSlots();

        // boot to an empty console: pulled down, library open, no ROM loaded
        overlay.classList.add("hidden");
        ejectCart();
      });
    }).catch(function(err){
      console.error(err);
      showError("Startup failed", (err && err.message) ? err.message : String(err));
    });
  }

  boot();
})();