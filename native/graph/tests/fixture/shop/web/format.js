// Formatting helpers for the fixture.

function formatPrice(cents) {
  return (cents / 100).toFixed(2);
}

function renderRow(item) {
  return item.name + ": " + formatPrice(item.cents);
}

module.exports = { formatPrice, renderRow };
