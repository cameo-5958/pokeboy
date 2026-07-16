(function(global){
  "use strict";

  global.createInputControls = function(options){
  var BTN = options.BTN;
  var DPAD = options.DPAD;
  /* ---------------- input ---------------- */
  var buttonState = 0, dpadState = 0;
  function setButton(mask, on){
    buttonState = on ? (buttonState | mask) : (buttonState & ~mask);
    syncInput();
  }
  function setDpad(mask, on){
    dpadState = on ? (dpadState | mask) : (dpadState & ~mask);
    syncInput();
  }
  function syncInput(){
    if(options.gb() && options.api.setInput) options.api.setInput(options.gb(), buttonState, dpadState);
  }

  function wireHoldButton(el, mask, isDpad, clickKind){
    var active = false;
    function down(e){
      e.preventDefault();
      active = true;
      el.classList.add("held");
      options.audio().playClick(clickKind || "btn");
      if(isDpad) setDpad(mask, true); else setButton(mask, true);
      try{ el.setPointerCapture(e.pointerId); }catch(err){}
    }
    function up(e){
      if(!active) return;
      active = false;
      el.classList.remove("held");
      options.audio().playClick("release");
      if(isDpad) setDpad(mask, false); else setButton(mask, false);
    }
    el.addEventListener("pointerdown", down, {passive:false});
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
    el.addEventListener("pointerleave", up);
    el.addEventListener("contextmenu", function(e){ e.preventDefault(); });
  }

  wireHoldButton(document.getElementById("btnA"), BTN.A, false, "btn");
  wireHoldButton(document.getElementById("btnB"), BTN.B, false, "btn");
  wireHoldButton(document.getElementById("btnStart"), BTN.START, false, "pill");
  wireHoldButton(document.getElementById("btnSelect"), BTN.SELECT, false, "pill");

  /* d-pad: single touch surface, tracks slide, supports diagonals */
  (function(){
    var pad = document.getElementById("dpad");
    var glowUp = document.getElementById("glowUp");
    var glowDown = document.getElementById("glowDown");
    var glowLeft = document.getElementById("glowLeft");
    var glowRight = document.getElementById("glowRight");
    var activePointer = null;
    var current = { up:false, down:false, left:false, right:false };

    function apply(next){
      if((next.up && !current.up) || (next.down && !current.down) ||
         (next.left && !current.left) || (next.right && !current.right)){
        options.audio().playClick("dpad");
      }
      if(next.up !== current.up){ setDpad(DPAD.UP, next.up); glowUp.classList.toggle("on", next.up); }
      if(next.down !== current.down){ setDpad(DPAD.DOWN, next.down); glowDown.classList.toggle("on", next.down); }
      if(next.left !== current.left){ setDpad(DPAD.LEFT, next.left); glowLeft.classList.toggle("on", next.left); }
      if(next.right !== current.right){ setDpad(DPAD.RIGHT, next.right); glowRight.classList.toggle("on", next.right); }
      current = next;
    }

    function clearAll(){ apply({up:false,down:false,left:false,right:false}); }

    function update(clientX, clientY){
      var rect = pad.getBoundingClientRect();
      var cx = rect.left + rect.width/2;
      var cy = rect.top + rect.height/2;
      var dx = clientX - cx;
      var dy = clientY - cy;
      var dead = rect.width * 0.16;
      var dist = Math.sqrt(dx*dx + dy*dy);
      if(dist < dead){ clearAll(); return; }
      var angle = Math.atan2(dy, dx); // radians, 0 = right, PI/2 = down
      var deg = angle * 180 / Math.PI;
      // 8-way split with generous diagonal wedges
      var next = {up:false,down:false,left:false,right:false};
      if(deg > -112.5 && deg < -67.5){ next.up = true; }
      else if(deg >= -67.5 && deg <= -22.5){ next.up = true; next.right = true; }
      else if(deg > -22.5 && deg < 22.5){ next.right = true; }
      else if(deg >= 22.5 && deg <= 67.5){ next.down = true; next.right = true; }
      else if(deg > 67.5 && deg < 112.5){ next.down = true; }
      else if(deg >= 112.5 && deg <= 157.5){ next.down = true; next.left = true; }
      else if(deg > 157.5 || deg < -157.5){ next.left = true; }
      else { next.up = true; next.left = true; }
      apply(next);
    }

    pad.addEventListener("pointerdown", function(e){
      e.preventDefault();
      activePointer = e.pointerId;
      try{ pad.setPointerCapture(e.pointerId); }catch(err){}
      update(e.clientX, e.clientY);
    }, {passive:false});
    pad.addEventListener("pointermove", function(e){
      if(e.pointerId !== activePointer) return;
      e.preventDefault();
      update(e.clientX, e.clientY);
    }, {passive:false});
    function release(e){
      if(e.pointerId !== activePointer) return;
      activePointer = null;
      if(current.up || current.down || current.left || current.right){
        options.audio().playClick("release");
      }
      clearAll();
    }
    pad.addEventListener("pointerup", release);
    pad.addEventListener("pointercancel", release);
  })();

  /* keyboard for desktop testing */
  var KEYMAP = {
    ArrowUp:["dpad",DPAD.UP], ArrowDown:["dpad",DPAD.DOWN],
    ArrowLeft:["dpad",DPAD.LEFT], ArrowRight:["dpad",DPAD.RIGHT],
    "z":["btn",BTN.A], "Z":["btn",BTN.A],
    "x":["btn",BTN.B], "X":["btn",BTN.B],
    "Enter":["btn",BTN.START], "Shift":["btn",BTN.SELECT]
  };
  /* Desktop state shortcuts: 1-3 pick a slot, F2 saves, F4 loads. Deliberately
     not F1 (browser help) or F5 (reload). */
  window.addEventListener("keydown", function(e){
    if(e.key >= "1" && e.key <= String(options.stateSlotCount)){
      e.preventDefault(); options.selectStateSlot(parseInt(e.key, 10)); return;
    }
    if(e.key === "F2"){ e.preventDefault(); options.saveState(); return; }
    if(e.key === "F4"){ e.preventDefault(); options.loadState(); return; }
    var m = KEYMAP[e.key]; if(!m) return;
    e.preventDefault();
    if(m[0]==="dpad") setDpad(m[1], true); else setButton(m[1], true);
  });
  window.addEventListener("keyup", function(e){
    var m = KEYMAP[e.key]; if(!m) return;
    if(m[0]==="dpad") setDpad(m[1], false); else setButton(m[1], false);
  });

  /* prevent double-tap zoom / gesture zoom on iOS */
  document.addEventListener("dblclick", function(e){ e.preventDefault(); }, {passive:false});
  document.addEventListener("gesturestart", function(e){ e.preventDefault(); }, {passive:false});
  var lastTouchEnd = 0;
  document.addEventListener("touchend", function(e){
    var now = Date.now();
    if(now - lastTouchEnd <= 300) e.preventDefault();
    lastTouchEnd = now;
  }, {passive:false});


    return {
      reset: function(){ buttonState = 0; dpadState = 0; syncInput(); }
    };
  };
})(window);
