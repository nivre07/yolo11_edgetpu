// Tap-to-dismiss confirmation dialog for save/confirm actions, plus a
// Yes/No confirm mode for actions that need an explicit go/no-go before
// running (e.g. committing staged inventory changes). Separate overlay
// (not the big #modalOverlay used for Settings/Inventory/Recipes/
// Analytics/History) so it can layer on top of that modal when triggered
// from a button inside it, e.g. Settings' Save buttons.
(function () {
  "use strict";

  const overlay = document.getElementById("dialogOverlay");
  const messageEl = document.getElementById("dialogMessage");
  const okBtn = document.getElementById("dialogOkBtn");
  const yesBtn = document.getElementById("dialogYesBtn");
  const noBtn = document.getElementById("dialogNoBtn");

  function close() {
    overlay.classList.remove("open");
  }

  function show(message) {
    messageEl.textContent = message;
    okBtn.style.display = "";
    yesBtn.style.display = "none";
    noBtn.style.display = "none";
    overlay.classList.add("open");
  }

  function confirm(message, onYes) {
    messageEl.textContent = message;
    okBtn.style.display = "none";
    yesBtn.style.display = "";
    noBtn.style.display = "";
    overlay.classList.add("open");

    const handleYes = () => {
      cleanup();
      close();
      onYes && onYes();
    };
    const handleNo = () => {
      cleanup();
      close();
    };
    function cleanup() {
      yesBtn.removeEventListener("click", handleYes);
      noBtn.removeEventListener("click", handleNo);
    }
    yesBtn.addEventListener("click", handleYes);
    noBtn.addEventListener("click", handleNo);
  }

  okBtn.addEventListener("click", close);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && overlay.classList.contains("open")) close();
  });

  window.Dialog = { show, close, confirm };
})();
