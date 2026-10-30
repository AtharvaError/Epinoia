// Accommodation allocator.
// Input: participants + contingents + rooms (hostels) + external stays + needs.
// Output: Decision<RoomAssignment[]> — recommendation + violations + relaxations
//         + cost-vs-cohesion tradeoffs + shortage report.
// Approach: greedy/constraint satisfaction over HARD rules first, then optimize
//           SOFT objectives; expose where supply < demand instead of overpacking.
// Never produces a silent "perfect" answer — only a defensible one.
export {};
