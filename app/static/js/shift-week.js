// Leader week page (/shifts/week). L1a only wires the print button; the people panel
// search and filters, selection colouring and the place popover come in L1b and L2.
// The week's data is in <script type="application/json" id="week-data">.
(() => {
  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-print-week]')) window.print();
  });
})();
