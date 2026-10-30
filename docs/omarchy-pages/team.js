"use strict";

// All cards ship as HTML. Filtering is an optional local enhancement.
const controls = document.querySelector(".filters");
const cards = Array.from(document.querySelectorAll(".roster-card"));
const search = document.getElementById("search");
const phase = document.getElementById("phase");
const stream = document.getElementById("stream");
function filterRoster() {
  const query = search.value.trim().toLowerCase();
  let visible = 0;
  for (const card of cards) {
    const matches = (!query || card.textContent.toLowerCase().includes(query)) &&
      (!phase.value || card.dataset.phase === phase.value) &&
      (!stream.value || card.dataset.stream === stream.value);
    card.hidden = !matches;
    if (matches) visible += 1;
  }
  document.getElementById("result-count").textContent = `${visible} of ${cards.length} contributors shown`;
  document.getElementById("no-results").hidden = visible !== 0;
}
for (const control of [search, phase, stream]) control.addEventListener("input", filterRoster);
document.getElementById("reset").addEventListener("click", () => {
  search.value = phase.value = stream.value = "";
  filterRoster();
  search.focus();
});
controls.hidden = false;
