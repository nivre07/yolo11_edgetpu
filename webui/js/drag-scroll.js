// Press-and-drag-to-scroll for every scrollable region, driven entirely by
// Pointer Events (fires uniformly for mouse, touch, and pen). Native
// touch-action/overflow-based scrolling depends on the browser recognizing
// a real multi-touch pan gesture; some touchscreen controllers instead
// report plain mouse events, where a press-drag over content does nothing
// but select text — which is exactly the symptom this replaces. Taking
// scrolling fully into our own hands here works identically regardless of
// what the underlying hardware/driver reports as.
(function () {
  "use strict";

  const SCROLL_SELECTOR = ".panel-body.scrollable, .detected-list, .modal-body, .browse-list";
  const DRAG_THRESHOLD = 6; // px of movement before a press counts as a drag-scroll, not a tap

  let target = null;
  let startY = 0;
  let startScrollTop = 0;
  let dragging = false;
  let suppressClickOn = null;

  document.addEventListener(
    "pointerdown",
    (e) => {
      const el = e.target.closest && e.target.closest(SCROLL_SELECTOR);
      if (!el) return;
      target = el;
      startY = e.clientY;
      startScrollTop = el.scrollTop;
      dragging = false;
    },
    { passive: true }
  );

  document.addEventListener(
    "pointermove",
    (e) => {
      if (!target) return;
      const dy = e.clientY - startY;
      if (!dragging && Math.abs(dy) < DRAG_THRESHOLD) return;
      dragging = true;
      target.scrollTop = startScrollTop - dy;
      e.preventDefault();
    },
    { passive: false }
  );

  function endDrag() {
    if (dragging && target) {
      // A press that moved is a scroll, not a tap — swallow the click that
      // would otherwise fire on whatever ended up under the pointer.
      suppressClickOn = target;
      requestAnimationFrame(() => {
        suppressClickOn = null;
      });
    }
    target = null;
    dragging = false;
  }
  document.addEventListener("pointerup", endDrag);
  document.addEventListener("pointercancel", endDrag);

  document.addEventListener(
    "click",
    (e) => {
      if (suppressClickOn && suppressClickOn.contains(e.target)) {
        e.preventDefault();
        e.stopPropagation();
      }
    },
    true
  );
})();
