// Inventory — logistics + merch, time- and location-aware.
// Item states: available | reserved | in_transit | damaged | awaiting_return.
// Merch rule: issued ONCE to the right person — enforce single-issue, block
//             duplicate claims (one of the live-test disruptions).
// Output: Decision<...> for moves/issues, with availability truthfully reported.
export {};
